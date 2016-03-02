#!/bin/sh -xe

set

. "$CI_SCRIPTS_DIR/build_local.sh"

echo "PWD: $(pwd)"
echo "Building PR#$CI_PULL_NR $CI_PULL_URL head: $CI_PULL_COMMIT..."

git clone $CI_BASE_REPO -b $CI_BASE_BRANCH build
cd build
if [ "$CI_BASE_REPO" != "$CI_PULL_REPO" ]; then
    git fetch $CI_PULL_REPO
fi

git reset --hard $CI_PULL_COMMIT
git rebase $CI_BASE_BRANCH

time build
