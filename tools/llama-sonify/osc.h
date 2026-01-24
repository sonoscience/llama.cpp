// Minimal OSC (Open Sound Control) sender - header-only implementation
// OSC 1.0 spec: http://opensoundcontrol.org/spec-1_0
//
// POSIX-only (macOS/Linux). For Windows support, would need Winsock.

#pragma once

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

#include <cstring>
#include <cstdint>
#include <string>

class OSCSender {
    int sock_ = -1;
    struct sockaddr_in dest_;
    bool valid_ = false;

public:
    OSCSender() = default;

    OSCSender(const char * host, int port) {
        init(host, port);
    }

    ~OSCSender() {
        if (sock_ >= 0) {
            close(sock_);
        }
    }

    // Non-copyable
    OSCSender(const OSCSender &) = delete;
    OSCSender & operator=(const OSCSender &) = delete;

    // Movable
    OSCSender(OSCSender && other) noexcept
        : sock_(other.sock_), dest_(other.dest_), valid_(other.valid_) {
        other.sock_ = -1;
        other.valid_ = false;
    }

    OSCSender & operator=(OSCSender && other) noexcept {
        if (this != &other) {
            if (sock_ >= 0) {
                close(sock_);
            }
            sock_ = other.sock_;
            dest_ = other.dest_;
            valid_ = other.valid_;
            other.sock_ = -1;
            other.valid_ = false;
        }
        return *this;
    }

    bool init(const char * host, int port) {
        sock_ = socket(AF_INET, SOCK_DGRAM, 0);
        if (sock_ < 0) {
            return false;
        }

        memset(&dest_, 0, sizeof(dest_));
        dest_.sin_family = AF_INET;
        dest_.sin_port = htons(port);

        if (inet_pton(AF_INET, host, &dest_.sin_addr) != 1) {
            close(sock_);
            sock_ = -1;
            return false;
        }

        valid_ = true;
        return true;
    }

    bool is_valid() const { return valid_; }

    // Send OSC message with a single float argument
    // OSC format: address (null-padded to 4-byte boundary), type tag ",f\0\0", float (big-endian)
    bool send_float(const char * address, float value) {
        if (!valid_) return false;

        char buf[256];
        size_t pos = 0;

        // Address string (null-terminated, padded to 4-byte boundary)
        size_t addr_len = strlen(address);
        if (addr_len >= 200) return false;  // sanity check

        memcpy(buf + pos, address, addr_len);
        pos += addr_len;

        // Null-pad to 4-byte boundary (at least one null)
        size_t padded = ((pos + 4) & ~3);
        while (pos < padded) {
            buf[pos++] = '\0';
        }

        // Type tag ",f" padded to 4 bytes
        buf[pos++] = ',';
        buf[pos++] = 'f';
        buf[pos++] = '\0';
        buf[pos++] = '\0';

        // Float value in big-endian (network byte order)
        uint32_t bits;
        memcpy(&bits, &value, sizeof(bits));
        bits = htonl(bits);
        memcpy(buf + pos, &bits, sizeof(bits));
        pos += sizeof(bits);

        ssize_t sent = sendto(sock_, buf, pos, 0,
                              (struct sockaddr *)&dest_, sizeof(dest_));
        return sent == (ssize_t)pos;
    }

    // Send OSC message with a single int32 argument
    bool send_int(const char * address, int32_t value) {
        if (!valid_) return false;

        char buf[256];
        size_t pos = 0;

        size_t addr_len = strlen(address);
        if (addr_len >= 200) return false;

        memcpy(buf + pos, address, addr_len);
        pos += addr_len;

        size_t padded = ((pos + 4) & ~3);
        while (pos < padded) {
            buf[pos++] = '\0';
        }

        // Type tag ",i"
        buf[pos++] = ',';
        buf[pos++] = 'i';
        buf[pos++] = '\0';
        buf[pos++] = '\0';

        // Int32 in big-endian
        uint32_t bits = htonl((uint32_t)value);
        memcpy(buf + pos, &bits, sizeof(bits));
        pos += sizeof(bits);

        ssize_t sent = sendto(sock_, buf, pos, 0,
                              (struct sockaddr *)&dest_, sizeof(dest_));
        return sent == (ssize_t)pos;
    }

    // Convenience: send projection value for a control vector at a specific layer
    // Address: /cv/{cv_name}/{layer}
    bool send_projection(const char * cv_name, int layer, float value) {
        char addr[128];
        snprintf(addr, sizeof(addr), "/cv/%s/%d", cv_name, layer);
        return send_float(addr, value);
    }

    // Convenience: send mean projection across all layers
    // Address: /cv/{cv_name}/mean
    bool send_projection_mean(const char * cv_name, float value) {
        char addr[128];
        snprintf(addr, sizeof(addr), "/cv/%s/mean", cv_name);
        return send_float(addr, value);
    }

    // Send generation status
    // Address: /generating
    bool send_generating(bool generating) {
        return send_int("/generating", generating ? 1 : 0);
    }
};
