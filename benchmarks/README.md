# SPy Benchmarks

Performance benchmarks for the SPy language, measuring wall-clock execution time across multiple compiler backends.

## Benchmarks

| File | What it measures |
|---|---|
| `fibonacci.spy` | Recursive function call overhead |
| `mandelbrot.spy` | Tight nested loops, floating-point math |
| `nbody.spy` | FP math, structs, loops (N-body simulation) |
| `nqueens.spy` | Recursion, backtracking, integer bitwise ops |

## Running Benchmarks

```bash
# Via pixi (all backends, 5 runs each)
pixi run benchmark

# Via Python directly
python benchmarks/run_benchmarks.py

# Custom runs and backends
python benchmarks/run_benchmarks.py --runs 10 --backend interp,doppler,C
```

## Adding a New Benchmark

1. Create a `.spy` file in this directory with a `def main() -> None:` entry point
2. The benchmark should print at least one value (to prevent the compiler from optimizing away the computation)
3. Run `pixi run benchmark` to include it

## Expected Output

```
Running 4 benchmarks, 5 runs each
Backends: interp, C
================================================

--- fibonacci ---
Backend      Min (s)      Mean (s)     Max (s)
------------------------------------------------
C            0.0123       0.0134       0.0156
interp       0.8543       0.8765       0.9012
...