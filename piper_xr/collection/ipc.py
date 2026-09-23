"""Portable array messages over an inherited local socket (no pickle)."""

import json
import struct

import numpy as np


def encode(metadata, arrays=None):
    chunks = []
    descriptors = {}
    for name, value in (arrays or {}).items():
        value = np.ascontiguousarray(value)
        if value.dtype.hasobject:
            raise ValueError("Object arrays are not supported")
        descriptors[name] = {"dtype": value.dtype.str, "shape": value.shape, "size": value.nbytes}
        chunks.append(value.tobytes())
    header = json.dumps({"metadata": metadata, "arrays": descriptors}, allow_nan=False).encode()
    return struct.pack("!I", len(header)) + header + b"".join(chunks)


def decode(payload):
    size = struct.unpack("!I", payload[:4])[0]
    header = json.loads(payload[4:4 + size])
    offset = 4 + size
    arrays = {}
    for name, desc in header["arrays"].items():
        dtype = np.dtype(desc["dtype"])
        if dtype.hasobject:
            raise ValueError("Object arrays are not supported")
        end = offset + desc["size"]
        arrays[name] = np.frombuffer(payload[offset:end], dtype=dtype).reshape(desc["shape"])
        offset = end
    if offset != len(payload):
        raise ValueError("Invalid array payload length")
    return header["metadata"], arrays


def send(connection, metadata, arrays=None):
    connection.send_bytes(encode(metadata, arrays))


def receive(connection):
    return decode(connection.recv_bytes())
