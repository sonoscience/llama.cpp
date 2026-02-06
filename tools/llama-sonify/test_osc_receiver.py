#!/usr/bin/env python3
"""
Simple OSC receiver for testing llama-sonify.

Usage:
    python test_osc_receiver.py [--port 9000]

Then run llama-sonify in another terminal:
    ./build/bin/llama-sonify -m model.gguf -p "Hello" --cv vector.gguf --osc-port 9000
"""

import argparse
import socket
import struct
import sys
from collections import defaultdict


def parse_osc_message(data: bytes) -> tuple[str, list]:
    """Parse an OSC message, returning (address, arguments)."""
    # Find end of address string (null-terminated, padded to 4 bytes)
    addr_end = data.index(b'\x00')
    address = data[:addr_end].decode('utf-8')

    # Skip to 4-byte boundary
    pos = (addr_end + 4) & ~3

    # Parse type tag string
    if pos >= len(data) or data[pos] != ord(','):
        return address, []

    type_tag_end = data.index(b'\x00', pos)
    type_tags = data[pos+1:type_tag_end].decode('utf-8')

    # Skip to 4-byte boundary
    pos = (type_tag_end + 4) & ~3

    # Parse arguments based on type tags
    args = []
    for tag in type_tags:
        if tag == 'f':  # float32
            value = struct.unpack('>f', data[pos:pos+4])[0]
            args.append(value)
            pos += 4
        elif tag == 'i':  # int32
            value = struct.unpack('>i', data[pos:pos+4])[0]
            args.append(value)
            pos += 4
        elif tag == 's':  # string
            str_end = data.index(b'\x00', pos)
            args.append(data[pos:str_end].decode('utf-8'))
            pos = (str_end + 4) & ~3

    return address, args


def main():
    parser = argparse.ArgumentParser(description='OSC receiver for llama-sonify testing')
    parser.add_argument('--port', type=int, default=9000, help='UDP port to listen on')
    parser.add_argument('--summary', action='store_true', help='Show summary stats instead of all messages')
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', args.port))

    print(f"Listening for OSC messages on port {args.port}...")
    print("Press Ctrl+C to stop\n")

    # Stats tracking
    msg_count = 0
    cv_stats = defaultdict(lambda: {'count': 0, 'min': float('inf'), 'max': float('-inf'), 'sum': 0})
    generating = False

    try:
        while True:
            data, addr = sock.recvfrom(1024)
            address, arguments = parse_osc_message(data)
            msg_count += 1

            if address == '/generating':
                generating = bool(arguments[0]) if arguments else False
                status = "STARTED" if generating else "STOPPED"
                print(f"[{msg_count:4d}] Generation {status}")

                # Print summary when generation stops
                if not generating and args.summary and cv_stats:
                    print("\n--- Summary ---")
                    for cv_name, stats in sorted(cv_stats.items()):
                        avg = stats['sum'] / stats['count'] if stats['count'] > 0 else 0
                        print(f"  {cv_name}: n={stats['count']}, min={stats['min']:.4f}, max={stats['max']:.4f}, avg={avg:.4f}")
                    print()
                    cv_stats.clear()

            elif address.startswith('/cv/'):
                # Parse /cv/{name}/{layer}
                parts = address.split('/')
                if len(parts) >= 4:
                    cv_name = parts[2]
                    layer = parts[3]
                    value = arguments[0] if arguments else 0.0

                    # Update stats
                    key = f"{cv_name}"
                    cv_stats[key]['count'] += 1
                    cv_stats[key]['min'] = min(cv_stats[key]['min'], value)
                    cv_stats[key]['max'] = max(cv_stats[key]['max'], value)
                    cv_stats[key]['sum'] += value

                    if not args.summary:
                        print(f"[{msg_count:4d}] {address}: {value:+.6f}")
            else:
                if not args.summary:
                    print(f"[{msg_count:4d}] {address}: {arguments}")

    except KeyboardInterrupt:
        print(f"\n\nReceived {msg_count} messages total")
        if cv_stats:
            print("\n--- Final Summary ---")
            for cv_name, stats in sorted(cv_stats.items()):
                avg = stats['sum'] / stats['count'] if stats['count'] > 0 else 0
                print(f"  {cv_name}: n={stats['count']}, min={stats['min']:.4f}, max={stats['max']:.4f}, avg={avg:.4f}")
    finally:
        sock.close()


if __name__ == '__main__':
    main()
