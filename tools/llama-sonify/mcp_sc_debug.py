#!/usr/bin/env python3
"""
MCP Server for SuperCollider / OSC debugging.

Provides tools to:
- Query and set scsynth control buses
- Send test OSC messages to the llama-sonify receiver
- Query synth node status

Usage:
    python mcp_sc_debug.py

Configure in Claude Code MCP settings to use this server.
"""

import asyncio
import json
import struct
import socket
import sys
from typing import Any

# scsynth default port
SCSYNTH_PORT = 57110
# Our OSC receiver port (where llama-sonify sends to)
OSC_RECEIVER_PORT = 9000


def build_osc_message(address: str, args: list[tuple[str, Any]]) -> bytes:
    """Build an OSC message.

    Args:
        address: OSC address (e.g., "/c_get")
        args: List of (type, value) tuples. Types: 'i'=int, 'f'=float, 's'=string
    """
    # Address (null-terminated, padded to 4 bytes)
    addr_bytes = address.encode('utf-8') + b'\x00'
    addr_bytes += b'\x00' * ((4 - len(addr_bytes) % 4) % 4)

    # Type tag
    type_tag = ',' + ''.join(t for t, v in args)
    type_bytes = type_tag.encode('utf-8') + b'\x00'
    type_bytes += b'\x00' * ((4 - len(type_bytes) % 4) % 4)

    # Arguments
    arg_bytes = b''
    for t, v in args:
        if t == 'i':
            arg_bytes += struct.pack('>i', int(v))
        elif t == 'f':
            arg_bytes += struct.pack('>f', float(v))
        elif t == 's':
            s = v.encode('utf-8') + b'\x00'
            s += b'\x00' * ((4 - len(s) % 4) % 4)
            arg_bytes += s

    return addr_bytes + type_bytes + arg_bytes


def parse_osc_message(data: bytes) -> tuple[str, list]:
    """Parse an OSC message, returning (address, arguments)."""
    addr_end = data.index(b'\x00')
    address = data[:addr_end].decode('utf-8')
    pos = (addr_end + 4) & ~3

    if pos >= len(data) or data[pos] != ord(','):
        return address, []

    type_tag_end = data.index(b'\x00', pos)
    type_tags = data[pos+1:type_tag_end].decode('utf-8')
    pos = (type_tag_end + 4) & ~3

    args = []
    for tag in type_tags:
        if tag == 'f':
            value = struct.unpack('>f', data[pos:pos+4])[0]
            args.append(value)
            pos += 4
        elif tag == 'i':
            value = struct.unpack('>i', data[pos:pos+4])[0]
            args.append(value)
            pos += 4
        elif tag == 's':
            str_end = data.index(b'\x00', pos)
            args.append(data[pos:str_end].decode('utf-8'))
            pos = (str_end + 4) & ~3

    return address, args


def send_osc_and_wait(host: str, port: int, message: bytes, timeout: float = 1.0) -> tuple[str, list] | None:
    """Send OSC message and wait for response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(message, (host, port))
        data, _ = sock.recvfrom(4096)
        return parse_osc_message(data)
    except socket.timeout:
        return None
    finally:
        sock.close()


def send_osc_no_wait(host: str, port: int, message: bytes) -> bool:
    """Send OSC message without waiting for response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(message, (host, port))
        return True
    except Exception as e:
        return False
    finally:
        sock.close()


# ============================================================
# MCP Tool implementations
# ============================================================

def sc_bus_get(bus_index: int) -> dict:
    """Query a control bus value from scsynth."""
    msg = build_osc_message('/c_get', [('i', bus_index)])
    result = send_osc_and_wait('127.0.0.1', SCSYNTH_PORT, msg)
    if result:
        addr, args = result
        if addr == '/c_set' and len(args) >= 2:
            return {"bus_index": args[0], "value": args[1]}
        return {"response": addr, "args": args}
    return {"error": "No response from scsynth (is it running?)"}


def sc_bus_set(bus_index: int, value: float) -> dict:
    """Set a control bus value on scsynth."""
    msg = build_osc_message('/c_set', [('i', bus_index), ('f', value)])
    success = send_osc_no_wait('127.0.0.1', SCSYNTH_PORT, msg)
    if success:
        return {"status": "sent", "bus_index": bus_index, "value": value}
    return {"error": "Failed to send to scsynth"}


def sc_node_query(node_id: int) -> dict:
    """Query a synth node's status."""
    msg = build_osc_message('/n_query', [('i', node_id)])
    result = send_osc_and_wait('127.0.0.1', SCSYNTH_PORT, msg)
    if result:
        addr, args = result
        if addr == '/n_info' and len(args) >= 6:
            return {
                "node_id": args[0],
                "parent": args[1],
                "prev": args[2],
                "next": args[3],
                "is_group": args[4],
                "head_or_synth": args[5] if len(args) > 5 else None
            }
        return {"response": addr, "args": args}
    return {"error": "No response from scsynth"}


def sc_status() -> dict:
    """Get scsynth server status."""
    msg = build_osc_message('/status', [])
    result = send_osc_and_wait('127.0.0.1', SCSYNTH_PORT, msg)
    if result:
        addr, args = result
        if addr == '/status.reply' and len(args) >= 7:
            return {
                "unused": args[0],
                "num_ugens": args[1],
                "num_synths": args[2],
                "num_groups": args[3],
                "num_synthdefs": args[4],
                "avg_cpu": args[5],
                "peak_cpu": args[6],
                "sample_rate": args[7] if len(args) > 7 else None,
                "actual_sample_rate": args[8] if len(args) > 8 else None
            }
        return {"response": addr, "args": args}
    return {"error": "No response from scsynth (is it running?)"}


def osc_send_cv(cv_name: str, layer: int, value: float, port: int = OSC_RECEIVER_PORT) -> dict:
    """Send a test /cv/{name}/{layer} message to the OSC receiver."""
    address = f"/cv/{cv_name}/{layer}"
    msg = build_osc_message(address, [('f', value)])
    success = send_osc_no_wait('127.0.0.1', port, msg)
    if success:
        return {"status": "sent", "address": address, "value": value, "port": port}
    return {"error": "Failed to send OSC message"}


def osc_send_generating(state: int, port: int = OSC_RECEIVER_PORT) -> dict:
    """Send a /generating message to the OSC receiver."""
    msg = build_osc_message('/generating', [('i', state)])
    success = send_osc_no_wait('127.0.0.1', port, msg)
    if success:
        return {"status": "sent", "address": "/generating", "state": state, "port": port}
    return {"error": "Failed to send OSC message"}


def osc_send_raw(address: str, args: list, port: int = OSC_RECEIVER_PORT) -> dict:
    """Send a raw OSC message. Args should be list of [type, value] pairs."""
    typed_args = []
    for arg in args:
        if isinstance(arg, list) and len(arg) == 2:
            typed_args.append((arg[0], arg[1]))
        elif isinstance(arg, int):
            typed_args.append(('i', arg))
        elif isinstance(arg, float):
            typed_args.append(('f', arg))
        elif isinstance(arg, str):
            typed_args.append(('s', arg))

    msg = build_osc_message(address, typed_args)
    success = send_osc_no_wait('127.0.0.1', port, msg)
    if success:
        return {"status": "sent", "address": address, "args": typed_args, "port": port}
    return {"error": "Failed to send OSC message"}


def sc_bus_get_multiple(bus_indices: list[int]) -> dict:
    """Query multiple control bus values at once."""
    results = {}
    for idx in bus_indices:
        r = sc_bus_get(idx)
        if "value" in r:
            results[idx] = r["value"]
        else:
            results[idx] = r
    return {"buses": results}


# ============================================================
# MCP Protocol Implementation
# ============================================================

TOOLS = [
    {
        "name": "sc_status",
        "description": "Get SuperCollider scsynth server status (CPU, synths, UGens, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "sc_bus_get",
        "description": "Query a control bus value from scsynth. Returns the current value of the specified bus index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bus_index": {
                    "type": "integer",
                    "description": "The control bus index to query"
                }
            },
            "required": ["bus_index"]
        }
    },
    {
        "name": "sc_bus_get_multiple",
        "description": "Query multiple control bus values at once.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bus_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of control bus indices to query"
                }
            },
            "required": ["bus_indices"]
        }
    },
    {
        "name": "sc_bus_set",
        "description": "Set a control bus value on scsynth. Use this to directly test if the synth responds to bus changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bus_index": {
                    "type": "integer",
                    "description": "The control bus index to set"
                },
                "value": {
                    "type": "number",
                    "description": "The value to set (float)"
                }
            },
            "required": ["bus_index", "value"]
        }
    },
    {
        "name": "sc_node_query",
        "description": "Query a synth node's status on scsynth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "integer",
                    "description": "The node ID to query (default synth group is 1)"
                }
            },
            "required": ["node_id"]
        }
    },
    {
        "name": "osc_send_cv",
        "description": "Send a test /cv/{name}/{layer} message to the OSC receiver (port 9000). Use this to simulate llama-sonify output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cv_name": {
                    "type": "string",
                    "description": "The CV name (e.g., 'empathy', 'optimism')"
                },
                "layer": {
                    "type": "integer",
                    "description": "The layer number (1-28 for Qwen 7B)"
                },
                "value": {
                    "type": "number",
                    "description": "The projection value (typically -3 to +3)"
                },
                "port": {
                    "type": "integer",
                    "description": "OSC port (default 9000)",
                    "default": 9000
                }
            },
            "required": ["cv_name", "layer", "value"]
        }
    },
    {
        "name": "osc_send_generating",
        "description": "Send a /generating message to signal start/stop of generation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "integer",
                    "description": "1 = generating started, 0 = stopped"
                },
                "port": {
                    "type": "integer",
                    "description": "OSC port (default 9000)",
                    "default": 9000
                }
            },
            "required": ["state"]
        }
    },
    {
        "name": "osc_send_raw",
        "description": "Send a raw OSC message with arbitrary address and arguments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "OSC address (e.g., '/test')"
                },
                "args": {
                    "type": "array",
                    "description": "Arguments as list of values (int, float, string) or [type, value] pairs",
                    "items": {}
                },
                "port": {
                    "type": "integer",
                    "description": "OSC port (default 9000)",
                    "default": 9000
                }
            },
            "required": ["address", "args"]
        }
    }
]


async def handle_request(request: dict) -> dict:
    """Handle an MCP JSON-RPC request."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "sc-debug",
                    "version": "0.1.0"
                }
            }
        }

    elif method == "notifications/initialized":
        # No response needed for notifications
        return None

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS
            }
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        try:
            if tool_name == "sc_status":
                result = sc_status()
            elif tool_name == "sc_bus_get":
                result = sc_bus_get(tool_args["bus_index"])
            elif tool_name == "sc_bus_get_multiple":
                result = sc_bus_get_multiple(tool_args["bus_indices"])
            elif tool_name == "sc_bus_set":
                result = sc_bus_set(tool_args["bus_index"], tool_args["value"])
            elif tool_name == "sc_node_query":
                result = sc_node_query(tool_args["node_id"])
            elif tool_name == "osc_send_cv":
                result = osc_send_cv(
                    tool_args["cv_name"],
                    tool_args["layer"],
                    tool_args["value"],
                    tool_args.get("port", OSC_RECEIVER_PORT)
                )
            elif tool_name == "osc_send_generating":
                result = osc_send_generating(
                    tool_args["state"],
                    tool_args.get("port", OSC_RECEIVER_PORT)
                )
            elif tool_name == "osc_send_raw":
                result = osc_send_raw(
                    tool_args["address"],
                    tool_args.get("args", []),
                    tool_args.get("port", OSC_RECEIVER_PORT)
                )
            else:
                result = {"error": f"Unknown tool: {tool_name}"}

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, indent=2)
                        }
                    ]
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"error": str(e)})
                        }
                    ],
                    "isError": True
                }
            }

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }


DEBUG = False  # Set to True to enable debug logging to stderr

def log_debug(msg: str):
    if DEBUG:
        sys.stderr.write(f"[sc-debug] {msg}\n")
        sys.stderr.flush()


def read_message() -> dict | None:
    """Read a JSON-RPC message with Content-Length header."""
    # Read headers
    content_length = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            log_debug("EOF on stdin")
            return None
        line_str = line.decode('utf-8')
        line_stripped = line_str.strip()
        log_debug(f"Header line: {repr(line_stripped)}")
        if not line_stripped:
            break  # Empty line = end of headers
        if line_stripped.lower().startswith('content-length:'):
            content_length = int(line_stripped.split(':')[1].strip())

    if content_length == 0:
        log_debug("No content-length found")
        return None

    # Read body
    body = sys.stdin.buffer.read(content_length)
    log_debug(f"Read body ({len(body)} bytes): {body[:100]}")
    return json.loads(body.decode('utf-8'))


def write_message(msg: dict):
    """Write a JSON-RPC message with Content-Length header."""
    body = json.dumps(msg).encode('utf-8')
    header = f"Content-Length: {len(body)}\r\n\r\n"
    sys.stdout.buffer.write(header.encode('utf-8'))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def main():
    """Run the MCP server on stdin/stdout."""
    while True:
        try:
            request = read_message()
            if request is None:
                break

            # Handle request synchronously (tools are fast)
            response = asyncio.run(handle_request(request))

            if response is not None:
                write_message(response)

        except json.JSONDecodeError as e:
            sys.stderr.write(f"JSON decode error: {e}\n")
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
