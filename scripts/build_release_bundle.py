"""Create quality-gated private GitHub Release assets."""

from sponsor_intel.releases import build_release_bundle


def main() -> None:
    """Build the default local release bundle."""

    bundle = build_release_bundle()
    for path in (*bundle.assets, bundle.checksums_path):
        print(path)


if __name__ == "__main__":
    main()
