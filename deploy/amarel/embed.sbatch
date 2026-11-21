#!/bin/bash
# Encodes the planned passages on one GPU; for a large plan, run as an array with one task per chunk:
#   sbatch --export=ALL,MODEL=arctic-embed-s ~/edison-amarel/embed/embed.sbatch
#   sbatch --array=0-24%4 --export=ALL,MODEL=qwen3-embedding-0.6b ~/edison-amarel/embed/embed.sbatch
#SBATCH --job-name=edison-embed
#SBATCH --partition=gpu
#SBATCH --constraint=adalovelace
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=02:00:00
#SBATCH --output=/home/%u/edison-amarel/logs/embed-%A_%a.out
set -euo pipefail
# Everything lives in the home directory, never on scratch.
BASE=$HOME/edison-amarel
WORK=$BASE/embed
MODEL=${MODEL:?set MODEL to a name in builder/embed.py MODELS}
# Node-local /tmp is held in RAM, so the unpacked 9 GB environment counts against --mem.
LOCAL=/tmp/$USER-embed-${SLURM_JOB_ID}-${SLURM_ARRAY_TASK_ID:-0}
trap 'rm -rf "$LOCAL"' EXIT

# The drafting job's proven environment (Python 3.12, torch, transformers), unpacked onto node-local disk as it does.
mkdir -p "$LOCAL"
tar -xf "$BASE/vllm-env.tar" -C "$LOCAL"
ENV=$LOCAL/vllm
export LD_LIBRARY_PATH=$ENV/cuda-compat:${LD_LIBRARY_PATH:-}
export HF_HUB_OFFLINE=1 EMBED_MODELS=$WORK/models PYTHONIOENCODING=utf-8
echo "$(date '+%F %T') node=$(hostname -s) card=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1) model=$MODEL"

cd "$WORK/code"
"$ENV/bin/python" -u embed.py encode --batch ${BATCH:-64} --store "$WORK/store" --model "$MODEL" ${SLURM_ARRAY_TASK_ID:+--chunk "$SLURM_ARRAY_TASK_ID"}
if [ -f "$WORK/queries.txt" ] && [ -z "${SLURM_ARRAY_TASK_ID:-}" ]; then
    "$ENV/bin/python" -u embed.py queries --batch ${BATCH:-64} --store "$WORK/store" --model "$MODEL" --queries "$WORK/queries.txt"
fi
echo "$(date '+%F %T') finished"
