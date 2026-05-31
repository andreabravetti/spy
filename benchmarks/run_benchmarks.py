"""
Benchmark runner for SPy programs.

Runs each .spy benchmark under multiple backends (interp, doppler, C) and reports
wall-clock execution time.

Prerequisites:
    Ensure libspy (the C runtime library) is built before running:
        pixi run make-libspy

Usage:
    python benchmarks/run_benchmarks.py [--runs N] [--backend interp,C]
"""
import subprocess
import sys
import time
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).parent
SPY_CLI = [sys.executable, "-m", "spy"]


def check_spy_available() -> None:
    """Verify that SPy is importable and functional."""
    try:
        proc = subprocess.run(
            SPY_CLI + ["--help"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print("ERROR: spy CLI is not working:")
            print(proc.stderr)
            sys.exit(1)
    except FileNotFoundError:
        print(
            "ERROR: spy is not available. "
            "Make sure the virtualenv is activated and spylang is installed."
        )
        sys.exit(1)


def compile_c(spy_file: Path) -> Path:
    """
    Compile a SPy file to a native executable using 'spy build'.
    Returns the path to the compiled executable.
    """
    compile_cmd = SPY_CLI + ["build", str(spy_file)]
    proc = subprocess.run(compile_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"ERROR compiling {spy_file.name} with C backend:")
        print(proc.stderr)
        sys.exit(1)

    # The build command prints "[debug] path/to/exe" on the last line
    lines = [l for l in proc.stdout.strip().split("\n") if l and not l.startswith("C files")]
    if not lines:
        print(f"ERROR: could not determine executable path for {spy_file.name}")
        sys.exit(1)
    exe_path = lines[-1]
    # Strip the build type prefix, e.g., "[debug] benchmarks/build/fibonacci"
    if "]" in exe_path:
        exe_path = exe_path.split("]", 1)[1].strip()
    return Path(exe_path)


def run_spy(spy_file: Path, backend: str, exe_path: Path | None = None) -> float:
    """
    Run a SPy file using the given backend.
    Returns the wall-clock time in seconds.
    """
    if backend == "interp":
        cmd = SPY_CLI + [str(spy_file)]
    elif backend == "doppler":
        cmd = SPY_CLI + ["redshift", str(spy_file)]
    elif backend == "C":
        assert exe_path is not None
        cmd = [str(exe_path)]
    else:
        raise ValueError(f"Unknown backend: {backend}")

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    t1 = time.perf_counter()

    if proc.returncode != 0:
        print(f"ERROR running {spy_file.name} with backend {backend}:")
        print(proc.stderr)
        sys.exit(1)

    return t1 - t0


def run_benchmark(spy_file: Path, backends: list[str], runs: int) -> dict:
    """
    Run a single benchmark multiple times across the given backends.
    Returns a dict with timing results.
    """
    results: dict[str, list[float]] = {}

    # If C backend is requested, compile once upfront
    exe_path: Path | None = None
    if "C" in backends:
        exe_path = compile_c(spy_file)

    for backend in backends:
        times: list[float] = []
        for _ in range(runs):
            elapsed = run_spy(spy_file, backend, exe_path=exe_path)
            times.append(elapsed)
        results[backend] = times
    return results


def print_results(name: str, results: dict[str, list[float]]) -> None:
    """
    Print benchmark results in a readable table format.
    """
    print(f"\n--- {name} ---")
    print(f"{'Backend':<12} {'Min (s)':<12} {'Mean (s)':<12} {'Max (s)':<12}")
    print("-" * 48)
    for backend in sorted(results.keys()):
        times = results[backend]
        mn = min(times)
        mx = max(times)
        mean = sum(times) / len(times)
        print(f"{backend:<12} {mn:<12.4f} {mean:<12.4f} {mx:<12.4f}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run SPy benchmarks across multiple backends"
    )
    parser.add_argument(
        "--runs", type=int, default=5,
        help="Number of runs per benchmark (default: 5)"
    )
    parser.add_argument(
        "--backend", type=str, default="C",
        help="Comma-separated list of backends: interp,doppler,C (default: C)"
    )
    parser.add_argument(
        "--interp", action="store_true",
        help="Also run with the interpreter backend"
    )
    parser.add_argument(
        "--doppler", action="store_true",
        help="Also run with the doppler (redshift) backend"
    )
    args = parser.parse_args()

    check_spy_available()

    backends = [b.strip() for b in args.backend.split(",")]
    if args.interp and "interp" not in backends:
        backends.append("interp")
    if args.doppler and "doppler" not in backends:
        backends.append("doppler")
    runs = args.runs

    spy_files = sorted(BENCHMARKS_DIR.glob("*.spy"))
    if not spy_files:
        print("No .spy benchmark files found.")
        sys.exit(1)

    print(f"Running {len(spy_files)} benchmarks, {runs} runs each")
    print(f"Backends: {', '.join(backends)}")
    print("=" * 48)

    for spy_file in spy_files:
        name = spy_file.stem
        results = run_benchmark(spy_file, backends, runs)
        print_results(name, results)

    print("\nDone.")


if __name__ == "__main__":
    main()