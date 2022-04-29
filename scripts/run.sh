#!/usr/bin/env bash

# This script is the default and basic run.sh example.

COLLECTING_JOBS_DELAY=1
NUM_JOBS=5
JOB_DELAY=1
POST_BUILD_DELAY=1

main() {
    echo "-- Working directory: $(pwd)"
    echo "-- Environment vars:"
    env

    local start_time=$(date +%s)
    echo "--- Start build"

    echo "--- Collecting jobs"
    sleep ${COLLECTING_JOBS_DELAY}

    echo "--- Running jobs"

    local retval=$(($RANDOM % 2))
    for job in $(seq 0 1 ${NUM_JOBS})
    do
        echo "---- Job ${job} started "
        sleep ${JOB_DELAY}
        echo "I am job ${job}" > job_${job}.txt
        echo "---- Job ${job} finished "
        echo
    done

    echo "--- Build completed (elapsed: $(($(date +%s) - ${start_time}))s, ret=${retval})"

    echo "-- Finalize"
    sleep ${POST_BUILD_DELAY}
    exit ${retval}
}

main
