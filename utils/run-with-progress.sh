#!/usr/bin/env bash

# This script is given as an example. It shows how a running job can interact
# with Murdock:
# - the job parameters (PR number, commit hash, etc) are retrieved via
#   environment variables
# - Each progress during the build job can be notified to the murdock API
#
# To use this script, set MURDOCK_SCRIPT_NAME=run-with-progress.sh in a .env
# file or to the server command line.


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
        STATUS='{"status" : {"status": "collecting jobs"}}'
        /usr/bin/curl -d "${STATUS}" -H "Content-Type: application/json" -H "Authorization: ${CI_JOB_TOKEN}" -X PUT ${CI_BASE_URL}/jobs/running/${CI_JOB_UID}/status > /dev/null
        sleep ${COLLECTING_JOBS_DELAY}

        echo "--- Running jobs"

        RETVAL=$(($RANDOM % 2))
        CANCELED=$(($RANDOM % 2))
        FAILED_BUILDS=""
        for job in $(seq 0 1 ${NUM_JOBS})
        do
            echo "---- Job ${job} started"
            echo "I am job ${job} (ret: ${RETVAL})" > job_${job}.txt
            if [ "${RETVAL}" -eq 1 ]
            then
                if  [ "${job}" -ne 0 ]
                then
                    FAILED_BUILDS+=", "
                fi
                FAILED_BUILDS+='{"name": "job_'${job}'", "href": "'${CI_BASE_URL}'/results/'${CI_JOB_UID}'/job_'${job}'.txt"}'
                STATUS='{"status" : {"status": "working", "total": '${NUM_JOBS}', "failed": '${job}', "passed": 0, "eta": "'$((${NUM_JOBS} - ${job}))'", "failed_builds": ['${FAILED_BUILDS}']}}'
            else
                STATUS='{"status" : {"status": "working", "total": '${NUM_JOBS}', "failed": 0, "passed": '${job}', "eta": "'$((${NUM_JOBS} - ${job}))'"}}'
            fi
            /usr/bin/curl -d "${STATUS}" -H "Content-Type: application/json" -H "Authorization: ${CI_JOB_TOKEN}" -X PUT ${CI_BASE_URL}/jobs/running/${CI_JOB_UID}/status > /dev/null
            sleep ${JOB_DELAY}
            echo "---- Job ${job} finished"
            echo
        done

        if [ "${RETVAL}" -eq 1 ] && [ "${CANCELED}" -eq 1 ]
        then
            STATUS='{"status" : {"status": "canceled", "failed_builds": ['${FAILED_BUILDS}']}}'
            /usr/bin/curl -d "${STATUS}" -H "Content-Type: application/json" -H "Authorization: ${CI_JOB_TOKEN}" -X PUT ${CI_BASE_URL}/jobs/running/${CI_JOB_UID}/status > /dev/null
        fi

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