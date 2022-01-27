#!/usr/bin/env bash

# This script is the default and basic run.sh example.

ACTION="$1"

COLLECTING_JOBS_DELAY=1
NUM_JOBS=5
JOB_DELAY=1
POST_BUILD_DELAY=1

case "$ACTION" in
    run)
        echo "-- Working directory: $(pwd)"
        echo "-- Environment vars:"
        env

        START_TIME=$(date +%s)
        echo "--- Start build"

        echo "--- Collecting jobs"
        sleep ${COLLECTING_JOBS_DELAY}

        echo "--- Running jobs"

        RETVAL=$(($RANDOM % 2))
        CANCELED=$(($RANDOM % 2))
        FAILED_BUILDS=""
        for job in $(seq 0 1 ${NUM_JOBS})
        do
            echo "---- Job ${job} started "
            sleep ${JOB_DELAY}
            echo "---- Job ${job} finished "
            echo
        done

        echo "--- Build completed (elapsed: $(($(date +%s) - ${START_TIME}))s, ret=${RETVAL})"
        exit $RETVAL
        ;;
    finalize)
        echo "-- Post build action"
        sleep ${POST_BUILD_DELAY}
        cat output.txt | /usr/bin/ansi2html > output.html
        exit 0
        ;;
    *)
        echo "$0: unhandled action $ACTION"
        exit 1
        ;;
esac
