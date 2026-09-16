import timeit

def _word32_swap_old(data: bytes) -> bytes:
    result = bytearray()
    for i in range(0, len(data), 4):
        chunk = data[i : i + 4]
        if len(chunk) < 4:
            chunk = chunk + b"\x00" * (4 - len(chunk))
        result.extend(reversed(chunk))
    return bytes(result)

def _word32_swap_new(data: bytes) -> bytes:
    result = bytearray()
    for i in range(0, len(data), 4):
        chunk = data[i : i + 4]
        if len(chunk) < 4:
            chunk = chunk + b"\x00" * (4 - len(chunk))
        result.extend(chunk[::-1])
    return bytes(result)

test_data = bytes([i % 256 for i in range(1024)])

n = 10000
start = timeit.default_timer()
for _ in range(n):
    _word32_swap_old(test_data)
end = timeit.default_timer()
old_time = end - start
print(f"Old time for {n} iterations: {old_time:.5f} seconds")

start = timeit.default_timer()
for _ in range(n):
    _word32_swap_new(test_data)
end = timeit.default_timer()
new_time = end - start
print(f"New time for {n} iterations: {new_time:.5f} seconds")

improvement = (old_time - new_time) / old_time * 100
speedup = old_time / new_time
print(f"Improvement: {improvement:.2f}%")
print(f"Speedup: {speedup:.2f}x")
