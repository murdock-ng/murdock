#!/bin/sh -ex

ACTION="$1"

case "$ACTION" in
    build)
        . "$CI_SCRIPTS_DIR/build_local.sh"

        echo "Building PR#$CI_PULL_NR $CI_PULL_URL head: $CI_PULL_COMMIT..."

        git clone $CI_BASE_REPO -b $CI_BASE_BRANCH build

        cd build
        if [ "$CI_BASE_REPO" != "$CI_PULL_REPO" ]; then
            git fetch origin pull/$CI_PULL_NR/head
        fi

        git checkout $CI_PULL_COMMIT
        git rebase $CI_BASE_BRANCH

        build || exit 1
        ;;
    post_build)
        cat output.txt | ansi2html -s solarized -u > output.html
        ;;
    *)
        echo "$0: unhandled action $ACTION"
        exit 1
esac
