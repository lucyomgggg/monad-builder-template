"""
Telos "builder-class" monad runtime: Telos search/write/stats, outbound HTTP (GET or full request),
workspace file tools, grep/glob, Python execution.
Fork this template and drive behavior with `config.yaml` (prompts, limits, `monad_id`).
"""

from builder_runtime.app import main


if __name__ == "__main__":
    main()
