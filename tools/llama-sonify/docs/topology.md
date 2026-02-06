

## Revised Architecture: Single Mac Studio

Given the callback infrastructure and the fact that the Mac Studio can run everything, we're for **collapsing the runtime stack onto one machine**. Here's why:

**Against network separation**:
- OSC over network adds jitter (even on local network, you're looking at 1-5ms variance)
- Debugging distributed systems is painful
- You'd need to sync model state, CV tensors, and audio parameters across machines

**For single-machine**:
- localhost OSC is sub-millisecond
- All state is local—easier to reason about
- The M3 Ultra especially has absurd headroom (128GB unified memory, massive bandwidth)
- Bitwig + llama.cpp + Python orchestration + Push 2 display rendering is well within capacity

**Proposed topology**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Mac Studio (M3 Ultra preferred, M2 Max works)                              │
│                                                                             │
│  ┌─────────────────────┐      ┌─────────────────────────────────────────┐  │
│  │  llama.cpp          │      │  Python orchestrator                    │  │
│  │  - GGUF model       │      │  - Receives l_out via ??? (see below)   │  │
│  │  - CV applied       │─────▶│  - Computes projections                 │  │
│  │  - cb_eval active   │      │  - Sends OSC to Bitwig                  │  │
│  └─────────────────────┘      │  - Renders Push 2 display               │  │
│                               └──────────────────┬──────────────────────┘  │
│                                                  │                          │
│                            ┌─────────────────────┼─────────────────────┐   │
│                            │                     │                     │   │
│                            ▼                     ▼                     ▼   │
│                    ┌─────────────┐      ┌─────────────┐      ┌───────────┐ │
│                    │  Bitwig     │      │  Push 2     │      │ Terminal  │ │
│                    │  (OSC in)   │      │  (USB)      │      │ (text)    │ │
│                    └─────────────┘      └─────────────┘      └───────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

Linux rigs (3060, 3090): CV generation, batch activation dumps, heavy offline work
```

---

## llama.cpp → Python Bridge: Do projection in C++, emit OSC directly**

Skip Python entirely for the hot path. C++ callback computes projections, sends OSC packets directly to Bitwig/wherever. Python only handles Push 2 display (which is lower frequency anyway).

OSC is the right abstraction layer—it's lightweight, well-supported, and gives you flexibility later without overengineering now.

This is essentially "cvector-generator's callback pattern, but running during inference and emitting OSC instead of accumulating for PCA."

---
