#!/usr/bin/env bash
# Install or remove the self-hosted GitHub Actions runner that runs the scrape.
#
#   scripts/runner.sh install   # download, register and start as a launchd service
#   scripts/runner.sh remove    # stop, unregister and delete (use when moving machines)
#
# Needs the `gh` CLI signed in as an account with admin rights on the repo.
# The runner gets the label the scrape workflow asks for, so any Mac registered
# this way can pick the job up.
set -euo pipefail

REPO="voralabs/prediction_mkt"
LABEL="arena-scraper"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-arena}"
NAME="${RUNNER_NAME:-$(scutil --get LocalHostName)}"

install_runner() {
  case "$(uname -m)" in
    arm64) arch="osx-arm64" ;;
    x86_64) arch="osx-x64" ;;
    *) echo "Unsupported CPU: $(uname -m)" >&2; exit 1 ;;
  esac

  version="$(gh api repos/actions/runner/releases/latest -q .tag_name | sed 's/^v//')"
  mkdir -p "$RUNNER_DIR"
  cd "$RUNNER_DIR"

  if [ ! -x ./config.sh ]; then
    echo "Downloading runner $version ($arch)..."
    curl -fsSL -o runner.tar.gz \
      "https://github.com/actions/runner/releases/download/v${version}/actions-runner-${arch}-${version}.tar.gz"
    tar xzf runner.tar.gz
    rm runner.tar.gz
  fi

  token="$(gh api -X POST "repos/$REPO/actions/runners/registration-token" -q .token)"
  ./config.sh --unattended --replace \
    --url "https://github.com/$REPO" --token "$token" \
    --name "$NAME" --labels "$LABEL"

  ./svc.sh install
  ./svc.sh start
  echo "Runner '$NAME' is running with label '$LABEL'."
}

remove_runner() {
  cd "$RUNNER_DIR"
  ./svc.sh stop || true
  ./svc.sh uninstall || true
  token="$(gh api -X POST "repos/$REPO/actions/runners/remove-token" -q .token)"
  ./config.sh remove --token "$token"
  cd ..
  rm -rf "$RUNNER_DIR"
  echo "Runner '$NAME' removed."
}

case "${1:-}" in
  install) install_runner ;;
  remove) remove_runner ;;
  *) echo "Usage: $0 install|remove" >&2; exit 1 ;;
esac
