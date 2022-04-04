#!/usr/bin/env bash

# This script is given as an example. It shows how a running job can interact
# with Murdock:
# - the job parameters (PR number, commit hash, etc) are retrieved via
#   environment variables
# - Each progress during the build job can be notified to the murdock API
#
# To use this script, set MURDOCK_SCRIPT_NAME=run-with-progress.sh in a .env
# file or to the server command line.

: ${COLLECTING_JOBS_DELAY:=3}
: ${NUM_JOBS:=10}
: ${JOB_DELAY:=1}
: ${POST_BUILD_DELAY:=3}

main() {
    echo "-- Working directory: $(pwd)"
    echo "-- Environment vars:"
    env

    local start_time=$(date +%s)
    echo "--- Start build"

    echo "--- Collecting jobs"
    local status='{"status" : {"status": "collecting jobs"}}'
    /usr/bin/curl -s -d "${status}" -H "Content-Type: application/json" -H "Authorization: ${CI_JOB_TOKEN}" -X PUT ${CI_BASE_URL}/job/${CI_JOB_UID}/status > /dev/null
    sleep ${COLLECTING_JOBS_DELAY}

    echo "--- Running jobs"

    local retval=$(($RANDOM % 2))
    local canceled=$(($RANDOM % 2))
    local failed_builds=""
    for job in $(seq 0 1 ${NUM_JOBS})
    do
        echo "---- Job ${job} started"
        echo "I am job ${job} (ret: ${retval})" > job_${job}.txt
        if [ "${retval}" -eq 1 ]
        then
            if  [ "${job}" -ne 0 ]
            then
                failed_builds+=", "
            fi
            failed_builds+='{"name": "job_'${job}'", "href": "'${CI_BASE_URL}'/results/'${CI_JOB_UID}'/job_'${job}'.txt"}'
            status='{"status" : {"status": "working", "total": '${NUM_JOBS}', "failed": '${job}', "passed": 0, "eta": "'$((${NUM_JOBS} - ${job}))'", "failed_builds": ['${failed_builds}']}}'
        else
            status='{"status" : {"status": "working", "total": '${NUM_JOBS}', "failed": 0, "passed": '${job}', "eta": "'$((${NUM_JOBS} - ${job}))'"}}'
        fi
        /usr/bin/curl -s -d "${status}" -H "Content-Type: application/json" -H "Authorization: ${CI_JOB_TOKEN}" -X PUT ${CI_BASE_URL}/job/${CI_JOB_UID}/status > /dev/null
        sleep ${JOB_DELAY}
        echo "---- Job ${job} finished"
        echo
    done

    if [ "${retval}" -eq 1 ] && [ "${canceled}" -eq 1 ]
    then
        status='{"status" : {"status": "canceled", "failed_builds": ['${failed_builds}']}}'
        /usr/bin/curl -s -d "${status}" -H "Content-Type: application/json" -H "Authorization: ${CI_JOB_TOKEN}" -X PUT ${CI_BASE_URL}/job/${CI_JOB_UID}/status > /dev/null
    fi

    echo "--- Build completed (elapsed: $(($(date +%s) - ${start_time}))s, ret=${retval})"
    echo "-- Finalize output"
    sleep ${POST_BUILD_DELAY}
    exit ${retval}
}

main
