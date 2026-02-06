## llama-sonify: Minimal Implementation Sketch

**New tool location**: `tools/sonify/` (or `examples/sonify/`)

**Core components**:

### 1. OSC Output (header-only library)

Rather than pulling in a full OSC library, we can use [oscpack](https://code.google.com/archive/p/oscpack/) or even hand-roll minimal UDP packet construction—OSC is a simple format:

```cpp
// Minimal OSC message: /proj/certainty/12 ,f 0.342
// Format: address (null-padded to 4-byte boundary), type tag, data

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

class OSCSender {
    int sock;
    struct sockaddr_in dest;
    
public:
    OSCSender(const char* host, int port) {
        sock = socket(AF_INET, SOCK_DGRAM, 0);
        dest.sin_family = AF_INET;
        dest.sin_port = htons(port);
        inet_pton(AF_INET, host, &dest.sin_addr);
    }
    
    void send_float(const char* address, float value) {
        char buf[256];
        size_t addr_len = strlen(address);
        size_t padded_addr_len = (addr_len + 4) & ~3;  // Pad to 4 bytes
        
        memset(buf, 0, sizeof(buf));
        memcpy(buf, address, addr_len);
        
        // Type tag ",f" padded
        size_t tag_offset = padded_addr_len;
        buf[tag_offset] = ',';
        buf[tag_offset + 1] = 'f';
        
        // Float value (big-endian)
        size_t val_offset = tag_offset + 4;
        uint32_t* fptr = (uint32_t*)&value;
        uint32_t be = htonl(*fptr);
        memcpy(buf + val_offset, &be, 4);
        
        sendto(sock, buf, val_offset + 4, 0, 
               (struct sockaddr*)&dest, sizeof(dest));
    }
    
    // Convenience: /cv/{name}/{layer}
    void send_projection(const char* cv_name, int layer, float value) {
        char addr[64];
        snprintf(addr, sizeof(addr), "/cv/%s/%d", cv_name, layer);
        send_float(addr, value);
    }
};
```

### 2. Control Vector Loading

Reuse the existing CV loading from llama.cpp—control vectors are already GGUF files with per-layer tensors:

```cpp
struct control_vector {
    std::string name;
    std::vector<std::vector<float>> layers;  // [n_layers][hidden_dim]
    
    float project(const float* activation, int layer) const {
        const auto& cv = layers[layer];
        float dot = 0.0f, norm_sq = 0.0f;
        for (size_t i = 0; i < cv.size(); i++) {
            dot += activation[i] * cv[i];
            norm_sq += cv[i] * cv[i];
        }
        return dot / sqrtf(norm_sq);
    }
};

std::vector<control_vector> load_control_vectors(const std::vector<std::string>& paths);
```

### 3. The Callback

```cpp
struct sonify_context {
    std::vector<control_vector> cvs;
    OSCSender* osc;
    int current_layer = 0;
};

static bool cb_eval(struct ggml_tensor * t, bool ask, void * user_data) {
    auto * ctx = (sonify_context *) user_data;
    
    // Only interested in layer outputs
    if (ask) {
        return strncmp(t->name, "l_out", 5) == 0;
    }
    
    // Extract layer index from tensor name (e.g., "l_out-15")
    int layer = atoi(t->name + 6);  // Skip "l_out-"
    
    // Copy tensor from GPU
    size_t n_floats = ggml_nelements(t);
    std::vector<float> activation(n_floats);
    ggml_backend_tensor_get(t, activation.data(), 0, n_floats * sizeof(float));
    
    // For sequence dimension, take the last token's activation
    // Shape is typically [hidden_dim, seq_len] or similar—need to verify
    int hidden_dim = t->ne[0];
    int seq_len = t->ne[1];
    float* last_token = activation.data() + (seq_len - 1) * hidden_dim;
    
    // Project onto each control vector and send
    for (const auto& cv : ctx->cvs) {
        float proj = cv.project(last_token, layer);
        ctx->osc->send_projection(cv.name.c_str(), layer, proj);
    }
    
    return true;
}
```

### 4. Main Loop Integration

Modify `examples/main/main.cpp` or create new `tools/sonify/sonify.cpp`:

```cpp
int main(int argc, char** argv) {
    // Parse args: model, CV paths, OSC host/port, normal llama args
    gpt_params params;
    // ... parse args ...
    
    // Set up OSC
    OSCSender osc(osc_host, osc_port);
    
    // Load control vectors
    std::vector<control_vector> cvs = load_control_vectors(cv_paths);
    
    // Set up sonification context
    sonify_context ctx { cvs, &osc, 0 };
    
    // Configure callback
    params.cb_eval = cb_eval;
    params.cb_eval_user_data = &ctx;
    
    // Normal llama initialization
    llama_model* model = llama_model_load(params.model.c_str(), params);
    llama_context* lctx = llama_context_init(model, params);
    
    // Generation loop (standard llama-cli logic)
    while (generating) {
        // ... tokenize, eval, sample ...
        // cb_eval fires automatically during eval, sends OSC
    }
    
    return 0;
}
```

---

## OSC Address Schema

Thinking about how Bitwig/Python will consume this:

```
/cv/{cv_name}/{layer}  float    # Per-layer projection
/cv/{cv_name}/mean     float    # Mean across layers (for simple mapping)
/cv/{cv_name}/final    float    # Final layer only
/token                 string   # Current token (for display sync)
/token/prob            float    # Token probability
/generating            int      # 1 = generating, 0 = stopped
```

The receiver can subscribe to whatever granularity it wants. Bitwig might just listen to `/cv/certainty/mean` for a single macro control, while the Push 2 display might consume all `/cv/*/` for visualization.

---

## Build Integration

For your fork, minimal CMake addition:

```cmake
# tools/sonify/CMakeLists.txt
add_executable(llama-sonify sonify.cpp osc.cpp cvector_load.cpp)
target_link_libraries(llama-sonify PRIVATE llama common)
```

Or just add to the existing Makefile if that's how you're building.

---

## First Milestone Checkpoint

**You'll know it's working when**:

1. `llama-sonify` runs, loads model + one CV
2. You give it a prompt
3. It generates text normally
4. Meanwhile, you see `/cv/certainty/0`, `/cv/certainty/1`, ... arriving at a UDP listener (netcat, Pure Data, or a Python test script)
5. Values change as generation proceeds

At that point, wiring to Bitwig is just DrivenByMoss configuration.

---

## Open Questions Before Implementation

1. **Tensor shape verification**: Need to confirm `l_out` tensor layout—is it `[hidden, seq]` or `[seq, hidden]`? The cvector-generator code or `examples/eval-callback` output will tell us.

2. **CV file format**: Are the CVs you've generated in the standard GGUF format that `llama_apply_adapter_cvec()` expects? If so, we might be able to reuse that loading code rather than writing custom parsing.

3. **Layer indexing**: Does `l_out-N` count from 0? Does it include embedding layer or just transformer blocks?

4. **Rate limiting**: If generation is fast, we might flood OSC. Worth adding a simple throttle (e.g., only emit every N ms) or let the receiver handle it?

Want me to flesh out any of these pieces further, or does this give you enough to start cutting code?