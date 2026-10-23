"""Read-only binding configuration inspection. Does not contact or change any VM."""
import argparse
import json

from .config import FeedSettings, load_bindings


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--allow-candidate", action="store_true")
    args = parser.parse_args()
    try:
        bindings, scope = load_bindings(args.path, allow_candidate=args.allow_candidate)
        settings = FeedSettings(bindings, "configuration-inspection-only", binding_scope=scope)
    except (OSError, ValueError) as error:
        # No config values or secrets are echoed on invalid input.
        parser.exit(2, f"Invalid binding configuration ({type(error).__name__}).\n")
    print(json.dumps({"scope": scope, "registry_verified": False,
                      "bindings": [b.model_dump(mode="json") for b in settings.bindings]}, ensure_ascii=False, indent=2))
