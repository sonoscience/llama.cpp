// llama-sonify: Stream neural activation projections via OSC during inference
//
// This tool intercepts tensor computations using llama.cpp's eval callback,
// projects activations onto control vectors, and sends the projections over
// UDP/OSC for real-time sonification or visualization.

#include "arg.h"
#include "common.h"
#include "log.h"
#include "llama.h"
#include "ggml.h"

#include "osc.h"

#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

// Control vector with projection capability
struct control_vector {
    std::string name;
    int n_embd;
    int n_layers;
    std::vector<float> data;  // Flat: [n_layers][n_embd], layer 1 at index 0

    // Get pointer to data for a specific layer (1-indexed)
    const float * layer_data(int layer) const {
        if (layer < 1 || layer > n_layers) return nullptr;
        return data.data() + (layer - 1) * n_embd;
    }

    // Project activation onto this control vector for a given layer
    // Returns normalized dot product (cosine-like but without normalizing activation)
    float project(const float * activation, int layer) const {
        const float * cv = layer_data(layer);
        if (!cv) return 0.0f;

        float dot = 0.0f;
        float norm_sq = 0.0f;
        for (int i = 0; i < n_embd; i++) {
            dot += activation[i] * cv[i];
            norm_sq += cv[i] * cv[i];
        }

        if (norm_sq < 1e-10f) return 0.0f;
        return dot / sqrtf(norm_sq);
    }
};

// Context passed to the eval callback
struct sonify_context {
    std::vector<control_vector> cvs;
    OSCSender * osc;
    std::vector<uint8_t> gpu_buffer;  // Reusable buffer for GPU->CPU copies
    int n_embd;  // Model embedding dimension
};

// Parse layer index from tensor name like "l_out-15" or "blk.15.l_out"
static int parse_layer_from_name(const char * name) {
    // Try "l_out-N" format first
    const char * p = strstr(name, "l_out-");
    if (p) {
        return atoi(p + 6);
    }

    // Try "l_out" with layer in "blk.N" prefix
    p = strstr(name, "blk.");
    if (p) {
        return atoi(p + 4);
    }

    return -1;
}

// Strip backend prefixes like "CUDA0#...#" from tensor names
static std::string filter_tensor_name(const char * name) {
    const char * p = strchr(name, '#');
    if (p) {
        p = p + 1;
        const char * q = strchr(p, '#');
        if (q) {
            return std::string(p, q - p);
        }
        return std::string(p);
    }
    return std::string(name);
}

// Eval callback: intercepts tensor computations during inference
static bool sonify_callback(struct ggml_tensor * t, bool ask, void * user_data) {
    auto * ctx = (sonify_context *)user_data;

    std::string name = filter_tensor_name(t->name);

    if (ask) {
        // Only interested in layer output tensors
        return name.find("l_out") != std::string::npos;
    }

    // Parse layer index
    int layer = parse_layer_from_name(name.c_str());
    if (layer < 0) {
        return true;  // Continue but skip this tensor
    }

    // Copy from GPU if needed
    const bool is_host = ggml_backend_buffer_is_host(t->buffer);
    uint8_t * data_ptr;

    if (!is_host) {
        size_t n_bytes = ggml_nbytes(t);
        ctx->gpu_buffer.resize(n_bytes);
        ggml_backend_tensor_get(t, ctx->gpu_buffer.data(), 0, n_bytes);
        data_ptr = ctx->gpu_buffer.data();
    } else {
        data_ptr = (uint8_t *)t->data;
    }

    // Tensor shape: ne[0] = hidden_dim, ne[1] = seq_len (typically)
    int64_t hidden_dim = t->ne[0];
    int64_t seq_len = t->ne[1];

    // Verify hidden dimension matches model
    if (hidden_dim != ctx->n_embd) {
        LOG_WRN("%s: tensor %s hidden_dim %" PRId64 " != n_embd %d, skipping\n",
                __func__, name.c_str(), hidden_dim, ctx->n_embd);
        return true;
    }

    // Get last token's activation (most relevant for autoregressive generation)
    // Stride: nb[1] is bytes per row (token)
    const float * last_token = (const float *)(data_ptr + (seq_len - 1) * t->nb[1]);

    // Project onto each control vector and send via OSC
    for (const auto & cv : ctx->cvs) {
        if (layer >= 1 && layer <= cv.n_layers) {
            float proj = cv.project(last_token, layer);
            ctx->osc->send_projection(cv.name.c_str(), layer, proj);
        }
    }

    return true;
}

// Load control vectors from files
static std::vector<control_vector> load_control_vectors(
    const std::vector<std::string> & paths,
    int n_embd) {

    std::vector<control_vector> result;

    for (const auto & path : paths) {
        // Use common library's loader
        std::vector<common_control_vector_load_info> load_info = {
            { 1.0f, path }  // strength = 1.0 (we normalize in projection)
        };

        common_control_vector_data cv_data = common_control_vector_load(load_info);

        if (cv_data.n_embd < 0) {
            LOG_ERR("%s: failed to load control vector from %s\n", __func__, path.c_str());
            continue;
        }

        if (cv_data.n_embd != n_embd) {
            LOG_ERR("%s: control vector %s has n_embd=%d, model has n_embd=%d\n",
                    __func__, path.c_str(), cv_data.n_embd, n_embd);
            continue;
        }

        control_vector cv;
        cv.n_embd = cv_data.n_embd;
        cv.n_layers = cv_data.data.size() / cv_data.n_embd;
        cv.data = std::move(cv_data.data);

        // Extract name from filename (strip path and extension)
        size_t slash = path.find_last_of("/\\");
        size_t dot = path.find_last_of('.');
        if (slash == std::string::npos) slash = 0; else slash++;
        if (dot == std::string::npos || dot < slash) dot = path.size();
        cv.name = path.substr(slash, dot - slash);

        LOG_INF("%s: loaded control vector '%s' with %d layers, n_embd=%d\n",
                __func__, cv.name.c_str(), cv.n_layers, cv.n_embd);

        result.push_back(std::move(cv));
    }

    return result;
}

static void print_usage(const char * prog) {
    LOG("\nUsage: %s [options]\n\n", prog);
    LOG("Options:\n");
    LOG("  -m, --model <path>      Path to model file (required)\n");
    LOG("  -p, --prompt <text>     Prompt to evaluate (required)\n");
    LOG("  --cv <path>             Control vector file (can specify multiple)\n");
    LOG("  --osc-host <host>       OSC destination host (default: 127.0.0.1)\n");
    LOG("  --osc-port <port>       OSC destination port (default: 9000)\n");
    LOG("  -h, --help              Show this help\n");
    LOG("\nStandard llama.cpp options (--threads, --ctx-size, etc.) are also supported.\n");
    LOG("\nExample:\n");
    LOG("  %s -m model.gguf -p \"Hello world\" --cv certainty.gguf --osc-port 9000\n\n", prog);
}

int main(int argc, char ** argv) {
    // Custom arguments
    std::vector<std::string> cv_paths;
    std::string osc_host = "127.0.0.1";
    int osc_port = 9000;

    // Pre-parse custom arguments before common_params_parse
    // (since common_params_parse doesn't know about --cv, --osc-*)
    std::vector<char *> filtered_argv;
    filtered_argv.push_back(argv[0]);

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--cv") == 0 && i + 1 < argc) {
            cv_paths.push_back(argv[++i]);
        } else if (strcmp(argv[i], "--osc-host") == 0 && i + 1 < argc) {
            osc_host = argv[++i];
        } else if (strcmp(argv[i], "--osc-port") == 0 && i + 1 < argc) {
            osc_port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            print_usage(argv[0]);
            return 0;
        } else {
            filtered_argv.push_back(argv[i]);
        }
    }

    // Parse standard llama params
    common_params params;
    if (!common_params_parse(filtered_argv.size(), filtered_argv.data(), params, LLAMA_EXAMPLE_COMMON)) {
        return 1;
    }

    // Validate required args
    if (params.prompt.empty()) {
        LOG_ERR("Error: --prompt is required\n");
        print_usage(argv[0]);
        return 1;
    }

    if (cv_paths.empty()) {
        LOG_WRN("Warning: no control vectors specified (use --cv). Running without projections.\n");
    }

    common_init();

    // Initialize OSC sender
    OSCSender osc;
    if (!osc.init(osc_host.c_str(), osc_port)) {
        LOG_ERR("Failed to initialize OSC sender to %s:%d\n", osc_host.c_str(), osc_port);
        return 1;
    }
    LOG_INF("OSC sender initialized: %s:%d\n", osc_host.c_str(), osc_port);

    // Initialize llama backend
    llama_backend_init();
    llama_numa_init(params.numa);

    // Disable warmup (we want to see all tensor callbacks)
    params.warmup = false;

    // Set up sonify context (CVs will be loaded after we know n_embd)
    sonify_context sonify_ctx;
    sonify_ctx.osc = &osc;
    sonify_ctx.n_embd = 0;  // Will be set after model load

    // Register eval callback BEFORE context creation
    params.cb_eval = sonify_callback;
    params.cb_eval_user_data = &sonify_ctx;

    // Initialize model and context
    common_init_result llama_init = common_init_from_params(params);

    llama_model * model = llama_init.model.get();
    llama_context * ctx = llama_init.context.get();

    if (model == nullptr || ctx == nullptr) {
        LOG_ERR("Failed to initialize llama model/context\n");
        return 1;
    }

    // Get model embedding dimension
    int n_embd = llama_model_n_embd(model);
    sonify_ctx.n_embd = n_embd;
    LOG_INF("Model n_embd: %d\n", n_embd);

    // Load control vectors (now that we know n_embd)
    sonify_ctx.cvs = load_control_vectors(cv_paths, n_embd);
    LOG_INF("Loaded %zu control vectors\n", sonify_ctx.cvs.size());

    // Print system info
    LOG_INF("\n%s\n", common_params_get_system_info(params).c_str());

    // Tokenize prompt
    const llama_vocab * vocab = llama_model_get_vocab(model);
    const bool add_bos = llama_vocab_get_add_bos(vocab);
    std::vector<llama_token> tokens = common_tokenize(ctx, params.prompt, add_bos);

    if (tokens.empty()) {
        LOG_ERR("No tokens to process (empty prompt?)\n");
        return 1;
    }

    LOG_INF("Prompt: \"%s\"\n", params.prompt.c_str());
    LOG_INF("Tokens: %zu\n", tokens.size());

    // Signal generation start
    osc.send_generating(true);

    // Evaluate prompt (callback fires during decode)
    LOG_INF("\nEvaluating...\n");
    if (llama_decode(ctx, llama_batch_get_one(tokens.data(), tokens.size()))) {
        LOG_ERR("Failed to decode\n");
        osc.send_generating(false);
        return 1;
    }

    // Signal generation end
    osc.send_generating(false);

    LOG_INF("\nDone.\n");
    llama_perf_context_print(ctx);

    llama_backend_free();

    return 0;
}
