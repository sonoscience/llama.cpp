#!/usr/bin/env python3
"""
Download control vectors from HuggingFace for llama-sonify testing.

Usage:
    source .venv/bin/activate
    pip install huggingface_hub
    python download_cvs.py [--list] [--all]
"""

import argparse
import os
from pathlib import Path

try:
    from huggingface_hub import hf_hub_download, list_repo_files
except ImportError:
    print("Please install huggingface_hub: pip install huggingface_hub")
    exit(1)

REPO_ID = "jukofyork/creative-writing-control-vectors-v3.0"
MODEL_NAME = "Qwen2.5-7B-Instruct"

# Control vectors we want for sonification testing
DESIRED_CVS = [
    # Empathy axis
    "empathy_vs_sociopathy__empathy.gguf",
    "empathy_vs_sociopathy__sociopathy.gguf",
    # Optimism axis
    "optimism_vs_nihilism__optimism.gguf",
    "optimism_vs_nihilism__nihilism.gguf",
    # Language axis
    "language__ornate.gguf",
    "language__simple.gguf",
]


def list_available_files(model_filter: str = None):
    """List all files in the repo, optionally filtered by model name."""
    print(f"Listing files in {REPO_ID}...")
    files = list_repo_files(REPO_ID)

    gguf_files = [f for f in files if f.endswith('.gguf')]

    if model_filter:
        gguf_files = [f for f in gguf_files if model_filter in f]

    # Group by model
    models = {}
    for f in gguf_files:
        parts = f.split('/')
        if len(parts) >= 2:
            model = parts[0]
            if model not in models:
                models[model] = []
            models[model].append(f)

    print(f"\nFound {len(gguf_files)} GGUF files across {len(models)} models:\n")

    for model in sorted(models.keys()):
        if model_filter and model_filter not in model:
            continue
        print(f"  {model}/ ({len(models[model])} files)")
        if model_filter or len(models) <= 5:
            for f in sorted(models[model]):
                print(f"    - {f.split('/')[-1]}")

    return gguf_files


def download_cvs(output_dir: Path, download_all: bool = False):
    """Download control vectors for the specified model."""
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching file list from {REPO_ID}...")
    all_files = list_repo_files(REPO_ID)

    # Find files for our model
    model_files = [f for f in all_files if MODEL_NAME in f and f.endswith('.gguf')]

    if not model_files:
        print(f"No files found for model {MODEL_NAME}")
        print("Available models:")
        models = set(f.split('/')[0] for f in all_files if f.endswith('.gguf'))
        for m in sorted(models):
            print(f"  - {m}")
        return []

    print(f"Found {len(model_files)} files for {MODEL_NAME}")

    # Filter to desired CVs unless downloading all
    if download_all:
        to_download = model_files
    else:
        to_download = []
        for f in model_files:
            filename = f.split('/')[-1]
            if any(cv in filename for cv in DESIRED_CVS):
                to_download.append(f)

    if not to_download:
        print("No matching control vectors found.")
        print("Available files:")
        for f in model_files:
            print(f"  - {f}")
        return []

    print(f"\nDownloading {len(to_download)} files to {output_dir}/\n")

    downloaded = []
    for repo_path in to_download:
        filename = repo_path.split('/')[-1]
        local_path = output_dir / filename

        if local_path.exists():
            print(f"  [skip] {filename} (already exists)")
            downloaded.append(local_path)
            continue

        print(f"  [download] {filename}...")
        try:
            hf_hub_download(
                repo_id=REPO_ID,
                filename=repo_path,
                local_dir=output_dir,
                local_dir_use_symlinks=False,
            )
            # hf_hub_download preserves directory structure, move file up
            nested_path = output_dir / repo_path
            if nested_path.exists() and nested_path != local_path:
                nested_path.rename(local_path)
                # Clean up empty dirs
                nested_path.parent.rmdir()
            downloaded.append(local_path)
            print(f"    -> {local_path}")
        except Exception as e:
            print(f"    ERROR: {e}")

    return downloaded


def main():
    parser = argparse.ArgumentParser(description='Download control vectors for llama-sonify')
    parser.add_argument('--list', action='store_true', help='List available files without downloading')
    parser.add_argument('--all', action='store_true', help='Download all CVs for the model (not just selected)')
    parser.add_argument('--output', type=Path, default=Path('cvs'), help='Output directory (default: cvs/)')
    parser.add_argument('--model', type=str, default=MODEL_NAME, help=f'Model name filter (default: {MODEL_NAME})')
    args = parser.parse_args()

    if args.list:
        list_available_files(args.model)
        return

    downloaded = download_cvs(args.output, download_all=args.all)

    if downloaded:
        print(f"\n✓ Downloaded {len(downloaded)} control vectors to {args.output}/")
        print("\nTo test with llama-sonify:")
        cv_args = ' '.join(f'--cv {p}' for p in downloaded[:3])
        print(f"  ./build/bin/llama-sonify -m model.gguf -p \"Hello\" {cv_args}")


if __name__ == '__main__':
    main()
