#!/usr/bin/env bash

# This script is given as an example. It shows how a running job can interact
# with Murdock:
# - the job parameters (PR number, commit hash, etc) are retrieved via
#   environment variables
# - Each progress during the build job can be notified to murdock on its /ctrl
#   endpoint

ACTION="$1"

COLLECTING_JOBS_DELAY=2
NUM_JOBS=30
JOB_DELAY=1

case "$ACTION" in
    build)
        echo "-- Working directory: $(pwd)"
        echo "-- Environment vars:"
        env

        START_TIME=$(date +%s)
        echo "--- Start build"

        echo "--- Collecting jobs"
        STATUS='{"status" : {"status": "collecting jobs"}}'
        /usr/bin/curl -d "${STATUS}" -H "Content-Type: application/json" -H "X-Murdock-Token: ${CI_API_TOKEN}" -X PUT ${CI_BASE_URL}/api/jobs/building/${CI_PULL_COMMIT}/status > /dev/null
        sleep ${COLLECTING_JOBS_DELAY}

        echo "--- Running jobs"

        RETVAL=$(($RANDOM % 2))
        CANCELED=$(($RANDOM % 2))
        FAILED_JOBS=""
        for job in $(seq 0 1 ${NUM_JOBS})
        do
            if [ "${RETVAL}" -eq 1 ]
            then
                if  [ "${job}" -ne 0 ]
                then
                    FAILED_JOBS+=", "
                fi
                FAILED_JOBS+='{"name": "job_'${job}'", "href": "job_'${job}'"}'
                STATUS='{"status" : {"status": "working", "total": '${NUM_JOBS}', "failed": '${job}', "passed": 0, "eta": "'$((${NUM_JOBS} - ${job}))'", "failed_jobs": ['${FAILED_JOBS}']}}'
            else
                STATUS='{"status" : {"status": "working", "total": '${NUM_JOBS}', "failed": 0, "passed": '${job}', "eta": "'$((${NUM_JOBS} - ${job}))'"}}'
            fi
            /usr/bin/curl -d "${STATUS}" -H "Content-Type: application/json" -H "X-Murdock-Token: ${CI_API_TOKEN}" -X PUT ${CI_BASE_URL}/api/jobs/building/${CI_PULL_COMMIT}/status > /dev/null
            sleep ${JOB_DELAY}
        done

        if [ "${RETVAL}" -eq 1 ] && [ "${CANCELED}" -eq 1 ]
        then
            STATUS='{"status" : {"status": "canceled", "failed_jobs": ['${FAILED_JOBS}']}}'
            /usr/bin/curl -d "${STATUS}" -H "Content-Type: application/json" -H "X-Murdock-Token: ${CI_API_TOKEN}" -X PUT ${CI_BASE_URL}/api/jobs/building/${CI_PULL_COMMIT}/status > /dev/null
        fi

        echo "--- Build completed (elapsed: $(($(date +%s) - ${START_TIME}))s, ret=${RETVAL})"
        exit $RETVAL
        ;;
    post_build)
        echo "-- Post build action"
        sleep 5
        cat output.txt | /usr/bin/ansi2html > output.html
        exit 0
        ;;
    *)
        echo "$0: unhandled action $ACTION"
        exit 1
        ;;
esac
