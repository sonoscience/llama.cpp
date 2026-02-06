# llama-sonify MCP Server Design

## Overview

An MCP (Model Context Protocol) server that wraps SuperCollider and llama-sonify, enabling collaborative exploration where Claude probes the system programmatically while the human provides phenomenological feedback on audio output.

**Core insight:** Claude can systematically vary parameters, track state, and reason about code. The human can report on perceptual salience, whether mappings "make sense," and catch things that sound wrong but aren't technically errors. The MCP server bridges these capabilities.

---

## Architecture

The MCP server communicates with a **running sclang instance** via OSC, rather than managing sclang as a subprocess. This is simpler, more reliable, and connects to your existing session with all buses and synths already defined.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Claude (MCP Client)                               │
│                                                                             │
│  • Calls tools to modify synthesis parameters                               │
│  • Triggers llama-sonify runs with specific prompts/CVs                     │
│  • Queries diagnostic state (bus values, node tree, OSC traffic)            │
│  • Maintains exploration history and hypotheses                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ MCP Protocol (stdio or SSE)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         llama-sonify MCP Server                             │
│                              (Python)                                       │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │ SC Bridge       │  │ OSC Monitor     │  │ Session Manager │             │
│  │                 │  │                 │  │                 │             │
│  │ • OSC to sclang │  │ • Receive from  │  │ • State log     │             │
│  │ • Execute code  │  │   llama-sonify  │  │ • Config snapshots           │
│  │ • Query state   │  │ • Traffic log   │  │ • Exploration   │             │
│  │ • Await results │  │                 │  │   history       │             │
│  └────────┬────────┘  └────────┬────────┘  └─────────────────┘             │
│           │                    │                                            │
└───────────┼────────────────────┼────────────────────────────────────────────┘
            │ OSC :7770          │ OSC :9000
            │ (send to sclang)   │ (receive from llama-sonify)
            ▼                    │
┌─────────────────┐              │
│    sclang       │◀─────────────┘
│  (user-managed, │    ┌─────────────────┐
│   running in    │    │  llama-sonify   │
│   SC IDE)       │    │   (C++ tool)    │
│                 │    └────────┬────────┘
│  Has MCP bridge │             │ OSC :9000
│  OSCdefs loaded │             │
└────────┬────────┘             │
         │ OSC                  │
         ▼                      ▼
┌─────────────────────────────────────────┐
│              scsynth                    │
│           (audio server)                │
└─────────────────────────────────────────┘
                    │
                    ▼
             ┌─────────────┐
             │ Audio Out   │
             │ (Scarlett)  │
             └─────────────┘
                    │
                    ▼
             ┌─────────────┐
             │   Human     │
             │ (listener)  │
             └─────────────┘
```

### Why OSC Instead of Subprocess?

| Aspect | OSC to running sclang | Subprocess approach |
|--------|----------------------|---------------------|
| Setup complexity | Lower (run bootstrap code once) | Higher (REPL parsing fragile) |
| Reliability | High (clean request/response) | Lower (prompt detection issues) |
| Access to existing state | Yes (`~cvBuses`, `~synth`, etc.) | No (fresh interpreter) |
| IDE integration | Works alongside SC IDE | Would conflict |
| Manual step required | Run bootstrap code once | None |

The manual step is minimal—just execute a code block in SC once per session.

---

## MCP Tools

### SuperCollider Control

#### `sc_execute`
Execute arbitrary SuperCollider code in the running sclang interpreter.

```json
{
  "name": "sc_execute",
  "description": "Execute SuperCollider code in sclang. Returns stdout/stderr. Use for SynthDef creation, synth instantiation, parameter changes, or any SC operation.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "code": {
        "type": "string",
        "description": "SuperCollider code to execute"
      },
      "description": {
        "type": "string", 
        "description": "Human-readable description of what this code does (for session log)"
      }
    },
    "required": ["code"]
  }
}
```

**Example calls:**
```python
# Create a new SynthDef
sc_execute(
    code="""
    SynthDef(\\testTone, { |freq=440, amp=0.1, out=0|
        Out.ar(out, SinOsc.ar(freq) * amp ! 2);
    }).add;
    """,
    description="Define simple test tone synth"
)

# Modify running synth parameter
sc_execute(
    code="~synth.set(\\lagTime, 0.2);",
    description="Increase smoothing to reduce clicks"
)

# Query bus value
sc_execute(
    code="~cvBuses[\\empathy].get({ |val| val.postln });",
    description="Check current empathy bus value"
)
```

#### `sc_node_tree`
Get the current server node tree (synths, groups, execution order).

```json
{
  "name": "sc_node_tree",
  "description": "Query the current scsynth node tree showing all running synths and groups with their execution order.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

#### `sc_server_status`
Get server status (running, CPU load, synth count, etc.).

```json
{
  "name": "sc_server_status",
  "description": "Query scsynth server status including: running state, CPU usage, number of synths, number of groups, sample rate.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

#### `sc_bus_poll`
Poll current value(s) from a control bus.

```json
{
  "name": "sc_bus_poll",
  "description": "Read current value(s) from a control bus. Useful for verifying OSC data is arriving.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "bus_name": {
        "type": "string",
        "description": "Name of bus in ~cvBuses dictionary, or raw bus index"
      },
      "num_channels": {
        "type": "integer",
        "description": "Number of channels to read (default 1)",
        "default": 1
      }
    },
    "required": ["bus_name"]
  }
}
```

#### `sc_synth_trace`
Enable tracing on a synth to see all internal UGen values.

```json
{
  "name": "sc_synth_trace",
  "description": "Trace a running synth, printing all internal UGen values. Useful for debugging signal flow.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "synth_name": {
        "type": "string",
        "description": "Name of synth variable (e.g., '~synth' or '~layerSynths[0]')"
      }
    },
    "required": ["synth_name"]
  }
}
```

---

### OSC Bridge

#### `osc_traffic`
Get recent OSC messages received from llama-sonify.

```json
{
  "name": "osc_traffic",
  "description": "Retrieve recent OSC messages from the traffic log. Useful for verifying llama-sonify is sending data.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "count": {
        "type": "integer",
        "description": "Number of recent messages to retrieve (default 20)",
        "default": 20
      },
      "filter_path": {
        "type": "string",
        "description": "Optional: only show messages matching this path prefix (e.g., '/cv/empathy')"
      }
    }
  }
}
```

#### `osc_send`
Send an OSC message directly to scsynth or sclang.

```json
{
  "name": "osc_send",
  "description": "Send an OSC message. Useful for testing or simulating llama-sonify data.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "description": "OSC address path (e.g., '/cv/empathy/mean')"
      },
      "args": {
        "type": "array",
        "description": "OSC arguments (floats, ints, strings)",
        "items": {}
      },
      "target": {
        "type": "string",
        "enum": ["sclang", "scsynth"],
        "description": "Target for the message (default sclang)",
        "default": "sclang"
      }
    },
    "required": ["path", "args"]
  }
}
```

**Example:** Simulate CV data without running llama-sonify:
```python
osc_send(path="/cv/empathy/mean", args=[0.7])
osc_send(path="/cv/optimism/mean", args=[0.3])
```

---

### llama-sonify Control

#### `llama_sonify_run`
Start a llama-sonify generation run.

```json
{
  "name": "llama_sonify_run",
  "description": "Start llama-sonify with specified parameters. Runs in background, streams OSC to SuperCollider.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "prompt": {
        "type": "string",
        "description": "Generation prompt"
      },
      "num_tokens": {
        "type": "integer",
        "description": "Number of tokens to generate (default 256)",
        "default": 256
      },
      "control_vectors": {
        "type": "array",
        "items": {"type": "string"},
        "description": "List of CV names to use (from available set)"
      },
      "osc_port": {
        "type": "integer",
        "description": "OSC port (default 9000)",
        "default": 9000
      }
    },
    "required": ["prompt"]
  }
}
```

#### `llama_sonify_stop`
Stop a running llama-sonify process.

```json
{
  "name": "llama_sonify_stop",
  "description": "Stop the currently running llama-sonify process.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

#### `llama_sonify_status`
Check if llama-sonify is running and get current state.

```json
{
  "name": "llama_sonify_status",
  "description": "Get status of llama-sonify: running/stopped, current prompt, tokens generated, active CVs.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

#### `llama_sonify_list_cvs`
List available control vectors.

```json
{
  "name": "llama_sonify_list_cvs",
  "description": "List available control vector files and their semantic meanings.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

---

### Session Management

#### `session_log`
View the exploration session log.

```json
{
  "name": "session_log",
  "description": "View the session log of actions taken, parameter changes, and observations.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "count": {
        "type": "integer",
        "description": "Number of recent entries (default all)",
        "default": -1
      }
    }
  }
}
```

#### `session_note`
Add a human observation or note to the session log.

```json
{
  "name": "session_note",
  "description": "Add a note to the session log (typically human observations about audio quality, perceptual effects, etc.)",
  "inputSchema": {
    "type": "object",
    "properties": {
      "note": {
        "type": "string",
        "description": "The observation or note to record"
      },
      "category": {
        "type": "string",
        "enum": ["observation", "hypothesis", "issue", "success", "idea"],
        "description": "Category for the note",
        "default": "observation"
      }
    },
    "required": ["note"]
  }
}
```

#### `session_snapshot`
Save current configuration as a named snapshot.

```json
{
  "name": "session_snapshot",
  "description": "Save the current SuperCollider state and configuration as a named snapshot for later recall.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Name for this snapshot"
      },
      "description": {
        "type": "string",
        "description": "Description of what this configuration achieves"
      }
    },
    "required": ["name"]
  }
}
```

#### `session_restore`
Restore a previously saved snapshot.

```json
{
  "name": "session_restore",
  "description": "Restore SuperCollider to a previously saved snapshot state.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Name of snapshot to restore"
      }
    },
    "required": ["name"]
  }
}
```

---

## MCP Resources

Resources provide read-only access to state that Claude might want to reference.

### `supercollider://synthdefs`
List of currently loaded SynthDef names and their parameter signatures.

### `supercollider://buses`  
Current bus allocations (names, indices, channel counts).

### `supercollider://state`
Current server state summary: synths running, buses allocated, recent OSC activity.

### `llama-sonify://config`
Current llama-sonify configuration: model path, available CVs, default parameters.

### `session://log`
Full session log as a resource.

### `session://snapshots`
List of available snapshots with descriptions.

---

## Implementation

### SuperCollider Bootstrap Code

Before using the MCP server, run this code block in SuperCollider once per session. It sets up OSC responders that the MCP server will communicate with:

```supercollider
// ============================================================
// Claude MCP Bridge for llama-sonify
// Run this once after booting the server
// ============================================================
(
var mcpPort = 7771;  // MCP server listens here for responses
~claudeMCP = NetAddr("127.0.0.1", mcpPort);

// Execute arbitrary SC code and return result
OSCdef(\claudeExecute, { |msg|
    var code = msg[1].asString;
    var requestId = msg[2].asString;
    var result;
    
    try {
        result = code.interpret;
        result = result.asString;
    } { |error|
        result = "ERROR: " ++ error.errorString;
    };
    
    ~claudeMCP.sendMsg('/claude/result', requestId, result);
}, '/claude/execute');

// Get bus value by name (from ~cvBuses dictionary)
OSCdef(\claudeGetBus, { |msg|
    var busName = msg[1].asSymbol;
    var requestId = msg[2].asString;
    var bus = ~cvBuses[busName];
    
    if(bus.notNil) {
        bus.get({ |val|
            ~claudeMCP.sendMsg('/claude/busValue', requestId, busName.asString, val);
        });
    } {
        ~claudeMCP.sendMsg('/claude/busValue', requestId, busName.asString, "NOT_FOUND");
    };
}, '/claude/getBus');

// Get all bus names and indices
OSCdef(\claudeListBuses, { |msg|
    var requestId = msg[1].asString;
    var busInfo = ~cvBuses.collect({ |bus, name| 
        [name, bus.index, bus.numChannels] 
    }).values.flat;
    
    ~claudeMCP.sendMsg('/claude/buses', requestId, *busInfo);
}, '/claude/listBuses');

// Get node tree as string
OSCdef(\claudeNodeTree, { |msg|
    var requestId = msg[1].asString;
    var tree = s.queryAllNodes(false);  // false = don't post to console
    
    // Capture tree output
    ~claudeMCP.sendMsg('/claude/nodeTree', requestId, tree.asString);
}, '/claude/nodeTree');

// Get server status
OSCdef(\claudeServerStatus, { |msg|
    var requestId = msg[1].asString;
    var status = [
        "running", s.serverRunning,
        "avgCPU", s.avgCPU,
        "peakCPU", s.peakCPU,
        "numSynths", s.numSynths,
        "numGroups", s.numGroups,
        "sampleRate", s.sampleRate
    ];
    
    ~claudeMCP.sendMsg('/claude/serverStatus', requestId, *status);
}, '/claude/serverStatus');

// Trace a synth (for debugging)
OSCdef(\claudeTraceSynth, { |msg|
    var synthName = msg[1].asString;
    var requestId = msg[2].asString;
    var synth;
    
    try {
        synth = synthName.interpret;
        if(synth.isKindOf(Synth)) {
            synth.trace;
            ~claudeMCP.sendMsg('/claude/traceResult', requestId, "OK: trace sent to post window");
        } {
            ~claudeMCP.sendMsg('/claude/traceResult', requestId, "ERROR: not a Synth");
        };
    } { |error|
        ~claudeMCP.sendMsg('/claude/traceResult', requestId, "ERROR: " ++ error.errorString);
    };
}, '/claude/traceSynth');

// Ping/health check
OSCdef(\claudePing, { |msg|
    var requestId = msg[1].asString;
    ~claudeMCP.sendMsg('/claude/pong', requestId, "ready");
}, '/claude/ping');

"Claude MCP bridge ready on port 7770".postln;
"Responses will be sent to port %".format(mcpPort).postln;
)
```

### Directory Structure

```
llama-sonify-mcp/
├── pyproject.toml
├── README.md
├── src/
│   └── llama_sonify_mcp/
│       ├── __init__.py
│       ├── server.py           # MCP server entry point
│       ├── sc_bridge.py        # OSC communication with sclang
│       ├── osc_monitor.py      # Monitor llama-sonify traffic
│       ├── llama_controller.py # llama-sonify process management
│       ├── session.py          # Session state, logging, snapshots
│       └── tools/
│           ├── __init__.py
│           ├── supercollider.py
│           ├── osc.py
│           ├── llama_sonify.py
│           └── session.py
├── sc/
│   └── mcp_bridge.scd          # Bootstrap code to run in SC
└── sessions/                    # Session logs and snapshots
```

### Key Dependencies

```toml
[project]
dependencies = [
    "mcp>=1.0.0",
    "python-osc>=1.8.0",
    "aiofiles>=23.0.0",      # Async file I/O for logging
]
```

Note: No `pexpect` needed—we're not managing sclang as a subprocess.

### SC Bridge (OSC-based)

Clean request/response over OSC with async awaiting of results:

```python
import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Any
from pythonosc import udp_client, dispatcher, osc_server

@dataclass
class PendingRequest:
    request_id: str
    future: asyncio.Future
    created_at: datetime

class SCBridge:
    def __init__(
        self, 
        sc_port: int = 7770,      # Port sclang listens on
        listen_port: int = 7771,  # Port we listen on for responses
        timeout: float = 5.0
    ):
        self.sc_client = udp_client.SimpleUDPClient("127.0.0.1", sc_port)
        self.listen_port = listen_port
        self.timeout = timeout
        self.pending: dict[str, PendingRequest] = {}
        self._server = None
        self._connected = False
        
    async def start(self):
        """Start the OSC server to receive responses from sclang."""
        disp = dispatcher.Dispatcher()
        disp.map("/claude/result", self._handle_result)
        disp.map("/claude/busValue", self._handle_bus_value)
        disp.map("/claude/buses", self._handle_buses)
        disp.map("/claude/nodeTree", self._handle_node_tree)
        disp.map("/claude/serverStatus", self._handle_server_status)
        disp.map("/claude/traceResult", self._handle_trace_result)
        disp.map("/claude/pong", self._handle_pong)
        
        self._server = osc_server.AsyncIOOSCUDPServer(
            ("127.0.0.1", self.listen_port),
            disp,
            asyncio.get_event_loop()
        )
        await self._server.create_serve_endpoint()
        
    async def check_connection(self) -> bool:
        """Ping sclang to verify the MCP bridge is loaded."""
        try:
            result = await self._request("/claude/ping", timeout=2.0)
            self._connected = (result == "ready")
            return self._connected
        except asyncio.TimeoutError:
            self._connected = False
            return False
    
    async def execute(self, code: str) -> str:
        """Execute SuperCollider code and return the result."""
        if not self._connected:
            await self.check_connection()
            if not self._connected:
                raise RuntimeError(
                    "SC MCP bridge not responding. "
                    "Please run the bootstrap code in SuperCollider."
                )
        
        return await self._request("/claude/execute", code)
    
    async def get_bus_value(self, bus_name: str) -> float | str:
        """Get current value of a named bus."""
        return await self._request("/claude/getBus", bus_name)
    
    async def list_buses(self) -> list[tuple[str, int, int]]:
        """Get all bus names, indices, and channel counts."""
        result = await self._request("/claude/listBuses")
        # Parse flat list into tuples
        buses = []
        for i in range(0, len(result), 3):
            buses.append((result[i], result[i+1], result[i+2]))
        return buses
    
    async def get_node_tree(self) -> str:
        """Get the current server node tree."""
        return await self._request("/claude/nodeTree")
    
    async def get_server_status(self) -> dict:
        """Get server status as a dictionary."""
        result = await self._request("/claude/serverStatus")
        # Parse flat key-value list into dict
        return dict(zip(result[::2], result[1::2]))
    
    async def trace_synth(self, synth_var: str) -> str:
        """Trace a synth (e.g., '~synth' or '~layerSynths[0]')."""
        return await self._request("/claude/traceSynth", synth_var)
    
    async def _request(self, path: str, *args, timeout: Optional[float] = None) -> Any:
        """Send OSC request and await response."""
        request_id = str(uuid.uuid4())[:8]
        future = asyncio.get_event_loop().create_future()
        
        self.pending[request_id] = PendingRequest(
            request_id=request_id,
            future=future,
            created_at=datetime.now()
        )
        
        try:
            self.sc_client.send_message(path, [*args, request_id])
            return await asyncio.wait_for(
                future, 
                timeout=timeout or self.timeout
            )
        finally:
            self.pending.pop(request_id, None)
    
    def _resolve(self, request_id: str, value: Any):
        """Resolve a pending request with its result."""
        if request_id in self.pending:
            self.pending[request_id].future.set_result(value)
    
    # OSC handlers
    def _handle_result(self, address, request_id, result):
        self._resolve(request_id, result)
        
    def _handle_bus_value(self, address, request_id, bus_name, value):
        self._resolve(request_id, value)
        
    def _handle_buses(self, address, request_id, *bus_info):
        self._resolve(request_id, list(bus_info))
        
    def _handle_node_tree(self, address, request_id, tree):
        self._resolve(request_id, tree)
        
    def _handle_server_status(self, address, request_id, *status):
        self._resolve(request_id, list(status))
        
    def _handle_trace_result(self, address, request_id, result):
        self._resolve(request_id, result)
        
    def _handle_pong(self, address, request_id, status):
        self._resolve(request_id, status)
```

### OSC Bridge

Receive OSC from llama-sonify, maintain traffic log, allow sending:

```python
from pythonosc import dispatcher, osc_server, udp_client
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import asyncio

@dataclass
class OSCMessage:
    timestamp: datetime
    path: str
    args: tuple

class OSCBridge:
    def __init__(self, receive_port: int = 9000, sc_port: int = 57120):
        self.traffic_log: deque[OSCMessage] = deque(maxlen=1000)
        self.receive_port = receive_port
        self.sc_client = udp_client.SimpleUDPClient("127.0.0.1", sc_port)
        self._server = None
        
    def _handle_message(self, path: str, *args):
        """Log incoming OSC message."""
        msg = OSCMessage(
            timestamp=datetime.now(),
            path=path,
            args=args
        )
        self.traffic_log.append(msg)
        
    async def start(self):
        """Start OSC receiver."""
        disp = dispatcher.Dispatcher()
        disp.set_default_handler(self._handle_message)
        
        self._server = osc_server.AsyncIOOSCUDPServer(
            ("127.0.0.1", self.receive_port),
            disp,
            asyncio.get_event_loop()
        )
        await self._server.create_serve_endpoint()
        
    def send(self, path: str, *args, target: str = "sclang"):
        """Send OSC message."""
        port = 57120 if target == "sclang" else 57110  # scsynth default
        client = udp_client.SimpleUDPClient("127.0.0.1", port)
        client.send_message(path, args)
        
    def get_traffic(self, count: int = 20, filter_path: str = None) -> list[OSCMessage]:
        """Get recent traffic, optionally filtered."""
        messages = list(self.traffic_log)[-count:]
        if filter_path:
            messages = [m for m in messages if m.path.startswith(filter_path)]
        return messages
```

### Session Manager

Track exploration history, save/restore configurations:

```python
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import json

@dataclass
class SessionEntry:
    timestamp: datetime
    action: str           # "sc_execute", "osc_send", "note", etc.
    details: dict
    category: str = "action"  # action, observation, hypothesis, etc.

@dataclass  
class Snapshot:
    name: str
    description: str
    timestamp: datetime
    sc_code: str          # SC code to recreate state
    cv_config: list[str]  # Active control vectors

class SessionManager:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        self.log: list[SessionEntry] = []
        self.snapshots: dict[str, Snapshot] = {}
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
    def record(self, action: str, details: dict, category: str = "action"):
        """Record an action or observation."""
        entry = SessionEntry(
            timestamp=datetime.now(),
            action=action,
            details=details,
            category=category
        )
        self.log.append(entry)
        self._persist_log()
        
    def add_note(self, note: str, category: str = "observation"):
        """Add a human note."""
        self.record("note", {"text": note}, category)
        
    def save_snapshot(self, name: str, description: str, sc_code: str, cv_config: list[str]):
        """Save current state as snapshot."""
        snapshot = Snapshot(
            name=name,
            description=description,
            timestamp=datetime.now(),
            sc_code=sc_code,
            cv_config=cv_config
        )
        self.snapshots[name] = snapshot
        self._persist_snapshots()
        
    def _persist_log(self):
        """Write log to disk."""
        log_file = self.session_dir / f"session_{self.session_id}.jsonl"
        with open(log_file, 'a') as f:
            entry = self.log[-1]
            f.write(json.dumps({
                "timestamp": entry.timestamp.isoformat(),
                "action": entry.action,
                "details": entry.details,
                "category": entry.category
            }) + "\n")
```

---

## Interaction Patterns

### Pattern 1: Debugging Silent Synths

```
Human: "Still no sound. OSC trace shows messages arriving."

Claude: [calls sc_node_tree]
        → Shows synth exists in node tree
        
Claude: [calls sc_bus_poll(bus_name="empathy")]
        → Returns 0.0 — bus not receiving data
        
Claude: [calls osc_traffic(filter_path="/cv/empathy")]
        → Shows messages arriving at /cv/empathy/12, /cv/empathy/13, etc.
        
Claude: "The OSC paths include layer numbers but the receiver expects /cv/empathy/mean.
         Let me fix the OSC handler..."
         
Claude: [calls sc_execute with updated OSCdef that aggregates layers]
Human: "Now I hear something! But there are clicks when the values change rapidly."

Claude: [calls session_note(note="Sound confirmed, clicks on rapid changes", category="observation")]

Claude: [calls sc_synth_trace(synth_name="~synth")]
        → Shows Lag.kr with lagTime=0.01
        
Claude: "The lag time is too short. Let me increase smoothing..."

Claude: [calls sc_execute(code="~synth.set(\\lagTime, 0.1);", description="Increase lag time")]Human: "Clicks are gone. But the empathy mapping feels too subtle - I can barely hear changes."

Claude: [calls session_note(note="Empathy mapping too subtle", category="observation")]

Claude: "Let me check the current mapping range and expand it..."

Claude: [calls sc_execute to query current mapping, then update to wider range]
```

### Pattern 2: Exploring Perceptual Mappings

```
Human: "I want to try mapping optimism to brightness instead of pitch."

Claude: [calls sc_execute to create new SynthDef variant]

Claude: [calls llama_sonify_run(prompt="Tell me about your hopes for the future", 
                                control_vectors=["optimism"])]
        
Human: "That is more intuitive! When the model talks about positive things, 
        the sound opens up. Save this."
        
Claude: [calls session_snapshot(name="optimism_brightness_v1", 
                                description="Optimism CV mapped to filter brightness")]
```

### Pattern 3: A/B Testing Synthesis Approaches

```
Human: "Let us compare additive vs granular for the layer activations."

Claude: [calls sc_execute to load additive SynthDef]
Claude: [calls llama_sonify_run with test prompt]
        
Human: "Additive sounds more coherent but less detailed."

Claude: [calls sc_execute to swap to granular SynthDef]
Claude: [calls llama_sonify_run with same prompt]
        
Human: "Granular captures more texture but feels chaotic. 
        Could we try a hybrid?"

Claude: [creates hybrid SynthDef combining both approaches]
```

---

## Workflow: First Session

A typical first exploration session might look like:

1. **Claude boots system**: `sc_execute` to start server, load bootstrap SynthDefs
2. **Verify connectivity**: `osc_send` test message, check `osc_traffic`
3. **Start simple**: Single CV (empathy), basic sine mapping
4. **Human feedback**: "Too quiet / too harsh / mapping unclear"
5. **Claude iterates**: Adjust parameters, try different mappings
6. **Find working config**: Human confirms "this is interesting"
7. **Save snapshot**: Preserve working state
8. **Expand complexity**: Add more CVs, richer synthesis
9. **Document findings**: Session log captures what worked and why

---

## Safety Considerations

### Preventing Audio Damage

- **Amplitude limiting**: All SynthDefs should include `Limiter.ar` before output
- **Default mute**: Synths start with amp=0, ramp up after confirmation
- **Kill switch**: `sc_execute("CmdPeriod.run")` stops all sound immediately
- **Human always has hardware volume control** as final safety

### State Management

- **Idempotent operations where possible**: Setting a parameter twice should be safe
- **Clear error messages**: If SC throws an error, surface it clearly
- **Graceful degradation**: If OSC traffic stops, synths should fade rather than freeze

### Session Boundaries

- **Auto-save session log** on any crash or disconnect
- **Snapshot before risky operations**: Prompt Claude to save state before major changes
- **Clear "undo" via snapshots**: Can always restore to known-good state

---

## Implementation Phases

### Phase 1: Core Functionality (MVP)
- [ ] SC Bridge with OSC request/response
- [ ] Basic `sc_execute` tool
- [ ] Connection health check (`sc_ping`)
- [ ] Bootstrap SC code file
- [ ] Session logging

### Phase 2: Diagnostic Tools
- [ ] `sc_node_tree`
- [ ] `sc_bus_poll`  
- [ ] `sc_list_buses`
- [ ] `sc_synth_trace`
- [ ] `sc_server_status`

### Phase 3: OSC Monitoring
- [ ] Monitor llama-sonify traffic
- [ ] `osc_traffic` tool (recent messages)
- [ ] `osc_send` tool (simulate data)

### Phase 4: llama-sonify Integration
- [ ] `llama_sonify_run` / `stop` / `status`
- [ ] CV listing and configuration
- [ ] Process management

### Phase 5: Session Management
- [ ] Snapshot save/restore
- [ ] Session notes
- [ ] Export session as reproducible script

### Phase 6: Polish
- [ ] MCP Resources
- [ ] Better error handling
- [ ] Documentation
- [ ] Example sessions

---

## Open Questions

1. **Timeout handling**: What should happen if sclang doesn't respond? Currently we timeout after 5 seconds—should we retry, or surface the error immediately?

2. **State synchronization**: If the human manually executes SC code in the IDE, the MCP server won't know about it. This is fine—we accept that the human has full control and the MCP server is just an assistant.

3. **Multi-user**: Could two Claudes (or a Claude and another tool) share the same MCP server? The request/response pattern with UUIDs should handle this, but untested.

4. **Audio analysis feedback**: Could the MCP server compute spectral features or loudness from the audio output and return them? This would give Claude indirect "hearing." Would require tapping the audio stream somehow.

5. **Bootstrap persistence**: Should we save the MCP bridge code to a file that auto-loads in the user's SC startup? Would eliminate the manual step but adds installation complexity.

---

## Next Steps

1. **Scaffold the project**: `uv init llama-sonify-mcp`, set up directory structure

2. **Implement SCBridge**: OSC send/receive with async request/response pattern

3. **Add basic tools**: `sc_execute`, `sc_server_status`, `sc_bus_poll`

4. **Create bootstrap code file**: `sc/mcp_bridge.scd` ready to run

5. **Test with existing llama-sonify setup**: Verify we can query buses, execute code, observe traffic

6. **Iterate based on actual usage**: Add tools as needed during real exploration sessions

---

*Design document prepared January 2026*
