from worker.app.runner import run_once

if __name__ == "__main__":
    result = run_once(run_limit=1)
    print(result)
