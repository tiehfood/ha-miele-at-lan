#!/usr/bin/env bash
# Append a "Contributors" section to a published GitHub release.
#
# Credits every GitHub account whose commits landed in the release, as an
# @mention, except the repository owner and bot accounts. Squash-merging a PR
# keeps the contributor as the commit author, so the commit -> account lookup
# is what makes this accurate; parsing "(#123)" out of subjects would miss
# commits landed any other way.
set -euo pipefail

VERSION="${1:?usage: release-contributors.sh <version>}"
TAG="v${VERSION}"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
OWNER="${GITHUB_REPOSITORY_OWNER:?GITHUB_REPOSITORY_OWNER is required}"

git fetch --tags --quiet

PREV_TAG="$(git tag --list 'v*' --sort=-v:refname | grep -vFx "$TAG" | head -n1 || true)"
if [ -n "$PREV_TAG" ]; then
  RANGE="${PREV_TAG}..${TAG}"
else
  RANGE="$TAG"
fi
echo "Collecting contributors over ${RANGE}"

logins=""
while read -r sha; do
  [ -n "$sha" ] || continue
  # The commit author is only linked to an account when the commit email belongs
  # to one. It often does not, so fall back to the author of the pull request the
  # commit came from before giving up on crediting someone.
  login="$(gh api "repos/${REPO}/commits/${sha}" --jq '.author.login // empty' 2>/dev/null || true)"
  if [ -z "$login" ]; then
    login="$(gh api "repos/${REPO}/commits/${sha}/pulls" --jq 'first(.[].user.login) // empty' 2>/dev/null || true)"
  fi
  [ -n "$login" ] || continue
  logins="${logins}${login}"$'\n'
done < <(git rev-list "$RANGE")

# Drop the owner and any bot account; semantic-release's own release commit is
# authored by a bot and must not show up as a contributor.
credited="$(printf '%s' "$logins" \
  | sed '/^$/d' \
  | sort -u \
  | grep -vix "$OWNER" \
  | grep -vixE 'semantic-release-bot|dependabot|.*\[bot\]' || true)"

if [ -z "$credited" ]; then
  echo "No external contributors in ${TAG} — release notes left unchanged."
  exit 0
fi

body_file="$(mktemp)"
gh release view "$TAG" --repo "$REPO" --json body --jq .body > "$body_file"
{
  printf '\n### Contributors\n\n'
  printf 'Thanks to everyone whose work is part of this release:\n\n'
  printf '* @%s\n' $credited
} >> "$body_file"

gh release edit "$TAG" --repo "$REPO" --notes-file "$body_file"
echo "Credited: $(printf '%s ' $credited)"
