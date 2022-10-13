from doctest import master
import json
import os
import xmltodict
import requests
import time
import datetime

WARNING_THRESHOLD = 80
FAIL_THRESHOLD = 50

# Remove 20 points if cert is expiring within 30 days
CERT_DAYS_REMAINING_THRESHOLD = 30
CERT_INVALIDATING_SOON_POINT_DEDUCTION = 20

# Remove 100 points if response code > 400
RESPONSE_CODE_THRESHOLD = 400
RESPONSE_CODE_POINT_DEDUCTION = 100

# Page is served over http not https
INSECURE_POINT_DEDUCTION = 80

# Remove 10 points if TTFB needs improvement
# Remove 15 points if TTFB is poor
TTFB_NEEDS_IMPROVEMENT_TIME_THRESHOLD = 800
TTFB_POOR_TIME_THRESHOLD = 1800
TTFB_NEEDS_IMPROVEMENT_POINT_DEDUCTION = 10
TTFB_POOR_POINT_DEDUCTION = 15

def ensure_full_url(input):
    if "http" in input or "https" in input:
        return input
    else:
        return default_root_url+input

def parse(filename):

    root_url = default_root_url # Could be overriden by servers block

    # Google supported .txt file sitemap format
    # One URL per line
    # https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap#text
    if filename.endswith(".txt"):
        with open(filename) as file:
            data = file.read()
            url_list = data.splitlines()
            for url in url_list:
                # Add to master list...
                url_string_list.append(ensure_full_url(url))
    # sitemap.xml parsing
    # https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap#xml
    elif filename.endswith(".xml"):
        with open(filename) as xml_file:
            sitemap_json = xmltodict.parse(xml_file.read())

            # Valid sitemap.xml will have an array of url objects inside the urlset
            if "urlset" in sitemap_json and "url" in sitemap_json['urlset']:
                urls = sitemap_json['urlset']['url']
                for url in urls:
                    # <loc> is the standard tag for an URL
                    # Add to master list
                    url_string_list.append(ensure_full_url(url['loc']))
    # Could be OpenAPI or Dynatrace endpoints.json format
    elif filename.endswith(".json"):
        with open(filename) as json_file:
            json_file = json.load(json_file)
        
            # Currently supported:
            # OpenAPI format or
            # Dynatrace Endpoints format

            # PARSE OPENAPI FORMAT
            if "openapi" in json_file:
                #print(f"Parsing JSON file and {filename} is an OpenAPI file")

                # If a servers block is given, can be multiple URLs so loop through each
                # TODO
                #if "servers" in json_file:
                    # TODO

                paths_available = json_file['paths'].keys()
            
                for path in paths_available:
                    # Add to master list
                    url_string_list.append(ensure_full_url(path))

                    path_full_info = json_file['paths'][path]
                    path_supported_http_method = path_full_info.keys()
        
            # PARSE DYNATRACE ENDPOINTS FORMAT
            # Note openapi has 'paths' too hence check that first and else if
            elif "paths" in json_file:
                #print(f"Parsing JSON file and {filename} is a Dynatrace Endpoints file")
                # Regardless of whether or not rootUrl is set,
                # add path (or full URL if it is all set in the path) to the url
                # Then add to master list
                for path in json_file['paths']:
                    #url += path['path']
                    url_string_list.append(ensure_full_url(path['path']))

            else:
                print(f"Parsing JSON file but {filename} is currently an unsupported format")


####################
# Start main logic #
####################

default_root_url = ""
config_file_name = "config.json"
directory_to_scan = ".dynatrace"
# Load config.json if absent, stop immediately
try:
    with open(f"{directory_to_scan}/{config_file_name}") as config_file:
      config_file_json = json.load(config_file)
except:
    print(f"{directory_to_scan}/{config_file_name} is missing or empty. .dynatrace/config.json must exist and have defaultRootUrl and a defaultLocations array. Cannot proceed. Please fix. Exiting...")
    exit(1)

try:
    default_root_url = config_file_json['defaultRootUrl']
    default_locations = config_file_json['defaultLocations']
except:
    print("Missing .dynatrace/config.json parameters. Cannot proceed. Exiting. Please see https://github.com/agardnerIT/dynatrace-endpoint-evaluator/blob/main/README.md")
    exit(1)

dt_environment_url = os.getenv("dt_environment_url","")
dt_api_token = os.getenv("dt_api_token","")

if dt_environment_url == "" or dt_api_token == "":
    print("DT_ENVIRONMENT_URL and / or DT_API_TOKEN is missing. Please create Git Action secrets for these. Cannot proceed. Exiting.")
    exit(1)

# Trim trailing slash from environment url if present
if dt_environment_url.endswith("/"):
    dt_environment_url = dt_environment_url[:-1]

file_list = os.scandir(directory_to_scan)
url_string_list = []

for file_or_dir in file_list:
    if os.path.isfile(file_or_dir.path) and config_file_name not in file_or_dir.path: parse(file_or_dir.path)

# Create a dictionary, using the List items as keys.
# This will automatically remove any duplicates because dictionaries cannot have duplicate keys.
url_string_list = list( dict.fromkeys(url_string_list) )

print(f"Will test the following URLs: {url_string_list}")

# Test URLs
# Just because the URLs are in the "to test" list, doesn't mean
# we need to create a synthetic on the tenant. It may already exist
working_list = []
for url in url_string_list:
    working_list.append({
        "endpoint": url,
        "monitor_id": "",
        "executions": []
    })

# Step 1: Get existing HTTP_CHECK tagged with `git-action`
# Remove items from working_list that already exist
headers = {
    "Authorization": f"Api-Token {dt_api_token}"
}
entity_selector = "type(HTTP_CHECK),tag(git-action)"

get_existing_synthetics_response = requests.get(
    url=f"{dt_environment_url}/api/v2/entities?entitySelector={entity_selector}",
    headers=headers
)
if get_existing_synthetics_response.status_code != 200:
    print("Couldn't get existing synthetics. Check your dt_environment_url and dt_api_token permissions. Cannot proceed. Exiting")
    exit(1)

existing_synthetics_json = get_existing_synthetics_response.json()

existing_synthetics = existing_synthetics_json['entities']

for existing_synthetic_http_check in existing_synthetics:
    print(existing_synthetic_http_check)
    monitor_id = existing_synthetic_http_check['entityId']
    existing_name = existing_synthetic_http_check['displayName']
    print(existing_name)
    if existing_name in url_string_list:
        print("Got a match. Do not need to recreate but do make a record of the monitor_id")
        found_item = [item for item in working_list if item['endpoint'] == existing_name][0]
        print(f"Item is: {found_item} and monitor ID is: {monitor_id}")

        # Set the monitor id on the object
        found_item['monitor_id'] = monitor_id

# working_list is now a list of items like:
# (where a test already exists in DT)
# {'endpoint': 'https://example.com/', 'monitor_id': 'HTTP_CHECK-AFA87AABE34655D4', 'executions': []}
# OR where a test does not exist and script needs to create one:
# {'endpoint': 'https://example.com/', 'monitor_id': '', executions: []}
#
# Note: Executions will always be empty at this point. They will be populated later

to_be_created_items = [item for item in working_list if item['monitor_id'] == ""]
for to_be_created in to_be_created_items:
    body = {
        "name": to_be_created['endpoint'],
	    "frequencyMin": 0,
	    "enabled": True,
	    "type": "HTTP",
	    "createdFrom": "API",
	    "script": {
    		"version": "1.0",
		    "requests": [{
    			"description": to_be_created['endpoint'],
			    "url": to_be_created['endpoint'],
			    "method": "GET",
			    "validation": {
    				"rules": [{
					    "value": ">=400",
					    "passIfFound": False,
					    "type": "httpStatusesList"
				    }]
			    },
			    "configuration": {
    				"acceptAnyCertificate": True,
				    "followRedirects": True,
				    "shouldNotPersistSensitiveData": True
			    }
		    }]
	    },
	    "locations": default_locations,
    	"anomalyDetection": {
		    "outageHandling": {
    			"globalOutage": True,
			    "globalOutagePolicy": {
    				"consecutiveRuns": 1
			    },
			    "localOutage": False,
			    "localOutagePolicy": {
    				"affectedLocations": None,
				    "consecutiveRuns": None
			    }
		    },
		    "loadingTimeThresholds": {
    			"enabled": True,
			    "thresholds": []
		    }
	    },
	    "tags": [{
    		"source": "USER",
		    "context": "CONTEXTLESS",
		    "key": "git-action"
	    }],
	    "managementZones": [],
	    "automaticallyAssignedApps": [],
	    "manuallyAssignedApps": [],
	    "requests": []
    }

    create_synthetic_response = requests.post(
        url=f"{dt_environment_url}/api/v1/synthetic/monitors",
        headers=headers,
        json=body
    )
    print(f"Creating synthetic: {to_be_created['endpoint']}")
    print(create_synthetic_response.status_code)
    print(create_synthetic_response.text)

    if create_synthetic_response.status_code != 200:
        print(f"Creation of synthetics failed. Response code: {create_synthetic_response.status_code}. Exiting.")
        exit(1)

    create_synthetic_response_json = create_synthetic_response.json()
    
    print(f"Successfully created: {create_synthetic_response_json['entityId']}")

    # Set the monitor_id for this newly created entityId
    to_be_created['monitor_id'] = create_synthetic_response_json['entityId']
# monitors to trigger
# If they are currently in Git, we trigger but may not create (they may already exist)
monitors_to_trigger = []
# Build monitors_to_trigger list
for item in working_list:
    monitors_to_trigger.append({
      "monitorId": item['monitor_id']
    })


# Step 2: Create a new synthetic for each above

print(f"-- Printing Complete List of Monitors to be Triggered (should be a complete list all with names and IDs) --")
print(monitors_to_trigger)
print("-----------------")

# Wait for some time so synthetics are created and registered in DT
# This is a best guess so we still need the loop below for certainty
if len(to_be_created_items) > 0:
    print(f"Waiting 60 seconds for synthetics to (hopefully) sync")
    time.sleep(60)

# Trigger a batch run based on tag
body = {
    "processingMode": "EXECUTIONS_DETAILS_ONLY",
    "failOnPerformanceIssue": "false",
    "stopOnProblem": "false",
    "monitors": monitors_to_trigger,
    "group": {}
}
    
batch_execution_response = requests.post(
    url=f"{dt_environment_url}/api/v2/synthetic/executions/batch",
    headers=headers,
    json=body
)
print(f"Batch Trigger Response Code: {batch_execution_response.status_code}")

batch_response_json = batch_execution_response.json()
#print(f"BRJSON: {batch_response_json}")

batch_id = batch_response_json['batchId']
print(f"Successfully triggered batch: {batch_id}")

batch_status = ""

must_retrigger_batch = False

while True:

    if must_retrigger_batch:
        must_retrigger_batch = False
        print(f"Retriggering batch (a new batch ID will be generated)...")
        batch_execution_response = requests.post(
            url=f"{dt_environment_url}/api/v2/synthetic/executions/batch",
            headers=headers,
            json=body
        )
        batch_response_json = batch_execution_response.json()
        print(f"Retriggered Batch Response JSON: {batch_response_json}")
        batch_id = batch_response_json['batchId']
        print(f"Batch Trigger Response Code: {batch_execution_response.status_code}")

        # Sleep for 30s after new batch trigger
        time.sleep(30)    

    get_batch_response = requests.get(
        url=f"{dt_environment_url}/api/v2/synthetic/executions/batch/{batch_id}",
        headers=headers
    )

    get_batch_response_json = get_batch_response.json()

    # If batch failed, exit immediately
    batch_status = get_batch_response_json['batchStatus']
    if batch_status == "FAILED" or batch_status == "FAILED_TO_EXECUTE":
        print(f"Batch failed with status: {batch_status}")
        break

    #print(f"Get Batch Response JSON: {get_batch_response_json}")

    if get_batch_response_json['triggeringProblemsCount'] == 0:
        # Everything triggered correctly. Breaking from loop.
        print(f"Batch: Everything triggered fine. Breaking from loop.")
        break
    else: # Problem triggering one or more synthetic tests.
        # Known potential causes:
        # 1. A new monitor has just been created and is not yet synced. In which case the batch will NOT auto-trigger, so we need to take care of that
        #    Detect that by looking at the cause in each triggeringProblems array. It will be: "Monitor's confiuguration is being synchronized. Please try in a moment."
        triggering_problems = get_batch_response_json['triggeringProblems']
        for triggering_problem in triggering_problems:
            cause = triggering_problem['cause']
            print(f"Triggering Problem Cause: {cause}")
            if "configuration is being synchronized. Please try in a moment."  in cause:
                print(f"New monitor(s) is / are still syncing. Wait and retrigger a new batch in 30s")
                must_retrigger_batch = True
                time.sleep(30)
                break
    
    print(f"Got triggering problems but must_retrigger_batch is false. Investigate. Raw output of triggering_problems: {triggering_problems}")

    print(f"Batch: {batch_id} is still in process. Response code: {get_batch_response.status_code}. Wait 30s and try again.")
    time.sleep(30)

# It is tempting to use the batch id to get details
# But if 1 of the URLs fails, the batch is listed as failing
# Instead, get the `triggered` array and for each, get the `executions` array then lookup each of those seperately.

if batch_status == "FAILED" or batch_status == "FAILED_TO_EXECUTE":
    print(f"Batch status was FAILED or FAILED_TO_EXECUTE. Investigate. Exiting. Batch Status was: {batch_status}")
    exit(1)

# If batch is still RUNNING, wait
while batch_status == "RUNNING":
    print(f"Batch status is still RUNNING. Wait for it to finish.")
    time.sleep(10)
    get_batch_response = requests.get(
        url=f"{dt_environment_url}/api/v2/synthetic/executions/batch/{batch_id}",
        headers=headers
    )

    get_batch_response_json = get_batch_response.json()
    batch_status = get_batch_response_json['batchStatus']

# After RUNNING, batch_status could be FAILED
if batch_status == "FAILED" or batch_status == "FAILED_TO_EXECUTE":
    print(f"Batch status ran but was FAILED or FAILED_TO_EXECUTE. Investigate. Exiting. Batch Status was: {batch_status}")
    exit(1)

triggered_executions = batch_response_json['triggered']

for triggered_entry in triggered_executions:
    # Get entry from working list that matches this monitorId
    triggered_monitor_id = triggered_entry['monitorId']
    print(f"Got triggered_monitor_id: {triggered_monitor_id}")

    matched_entry = [item for item in working_list if item['monitor_id'] == triggered_monitor_id][0]
    matched_entry['executions'] = triggered_entry['executions']

print("-------------------")
print("Printing working list as it now stands. All entries should have monitor_ids and executions set")
print(f"Length of working list (should be equal to number of endpoints): {len(working_list)}")

execution_results = []
for entry in working_list:
    executions = entry['executions']
    for execution in executions:
        execution_id = execution['executionId']
        # "executionStage" can be:
        # TRIGGERED = triggered but nothing else has happened yet
        # EXECUTED but also check the fullResults.status as it could be FAILED
        # DATA_RETRIEVED with fullResults.status is SUCCESS is the best case scenario
        execution_status = ""

        # Strangely "executionStage" remains TRIGGERED and fullResults.status is SUCCESS
        # But len(executionSteps) is zero if the URL doesn't exist
        while execution_status == "" or execution_status != "DATA_RETRIEVED":
            if execution_status != "DATA_RETRIEVED":
                print("Execution is still running or has run but script must wait for DATA_RECEIVED signal. Sleeping before trying again...")
                time.sleep(10)

            execution_details_response = requests.get(
                url=f"{dt_environment_url}/api/v2/synthetic/executions/{execution_id}/fullReport",
                headers=headers
            )

            execution_details_response_json = execution_details_response.json()
            execution_status = execution_details_response_json['executionStage']
        
        # Now we have an execution result. Good or bad we don't know yet
        execution_results.append(execution_details_response_json)
    
        print(f"Got results for {execution_id}. Moving onto the next execution...")

print("=================================================================================")
print(f"All done. Got {len(execution_results)} execution results. Processing them now...")

# build array of results which will be output to a file on the runner
# to be read by a subsequent GitHub Action job
results = []

for execution in execution_results:
    # Every execution starts perfect, with 100 points.
    # Rules
    # If response code > 400 = -100 points (page was missing or had a server error)
    # If no SSL cert (eg. http://), this is bad = -20 points
    # If SSL cert expires within CERT_DAYS_REMAINING_THRESHOLD days = -20 points
    # If responseStatusCode is 404 = -100 points and exit (cannot judge page as it is missing)
    #
    # ttfb (https://web.dev/ttfb/#what-is-a-good-ttfb-score)
    # 0 - 800ms = good (-0 points)
    # 801 - 1800 = needs improvement (-10 points)
    # >1801 = poor (-15 points)
    # 
    # FCP (https://web.dev/fcp/#what-is-a-good-fcp-score)
    # 0 - 1800ms = good (-0 points)
    # 1801ms - 3000ms = needs improvement (-10 points)
    # > 3000ms = poor (-15 points)
    #
    # Be great to get number of objects loaded split by type (JS vs. CSS vs. Images etc.)
    points = 100

    status = execution["fullResults"]["status"]
    execution_steps = execution["fullResults"]["executionSteps"]
    for step in execution_steps:

        step_name = step["requestName"]

        # Record any reasons why we reduce the score
        score_reduction_reasons = []

        is_insecure = False
        if step_name.startswith("http://") or step["peerCertificateDetails"] == "":
            print(f"{step['requestName']} is insecure")
            is_insecure = True

        step_response_status_code = step["responseStatusCode"]
        step_total_time = step["totalTime"]
        step_host_name_resolution_time = step["hostNameResolutionTime"]
        step_tcp_connect_time = step["tcpConnectTime"]
        step_tls_handshake_time = step["tlsHandshakeTime"]
        step_ttfb = step["timeToFirstByte"]
        step_tcp_connect_time = step["tcpConnectTime"]
        step_certificate_expiry_date = step["peerCertificateExpiryDate"]

        # Convert millis to a datetime object for when cert expires
        cert_expiry = datetime.datetime.fromtimestamp(step_certificate_expiry_date/1000)
        #print(f"{step_certificate_expiry_date}")
        #print(cert_expiry)

        time_now = datetime.datetime.now()
        # Calculate days between now and cert_expiry
        cert_days_remaining = (cert_expiry - time_now).days
        #print(f"Time Now: {time_now}. Expiry time: {cert_expiry}. Delta: {cert_days_remaining}")

        # Deduct points
        # Insecure page (http)
        if is_insecure:
            score_reduction_reasons.append(f"Removing {INSECURE_POINT_DEDUCTION} points from {step_name} because page is insecure (served over http not https)")
            print(f"Removing {INSECURE_POINT_DEDUCTION} points from {step_name} because page is insecure (served over http not https)")
            points -= INSECURE_POINT_DEDUCTION

        # Missing or error pages
        if step_response_status_code > RESPONSE_CODE_THRESHOLD:
            score_reduction_reasons.append(f"Removing {RESPONSE_CODE_POINT_DEDUCTION} points because response status > {RESPONSE_CODE_THRESHOLD}")
            print(f"Removing {RESPONSE_CODE_POINT_DEDUCTION} points from {step_name} because response status > {RESPONSE_CODE_THRESHOLD}")
            points -= RESPONSE_CODE_POINT_DEDUCTION

        # TTFB
        if step_ttfb > TTFB_POOR_TIME_THRESHOLD:
            score_reduction_reasons.append(f"Removing {TTFB_POOR_POINT_DEDUCTION} points from {step_name} because TTFB > {TTFB_POOR_TIME_THRESHOLD}")
            print(f"Removing {TTFB_POOR_POINT_DEDUCTION} points from {step_name} because TTFB > {TTFB_POOR_TIME_THRESHOLD}")
            points -= TTFB_POOR_POINT_DEDUCTION
        elif step_ttfb > TTFB_NEEDS_IMPROVEMENT_TIME_THRESHOLD:
            score_reduction_reasons.append(f"Removing {TTFB_NEEDS_IMPROVEMENT_POINT_DEDUCTION} points from {step_name} because TTFB > {TTFB_NEEDS_IMPROVEMENT_TIME_THRESHOLD}")
            print(f"Removing {TTFB_NEEDS_IMPROVEMENT_POINT_DEDUCTION} points from {step_name} because TTFB > {TTFB_NEEDS_IMPROVEMENT_TIME_THRESHOLD}")
            points -= TTFB_NEEDS_IMPROVEMENT_POINT_DEDUCTION
        # FCP
        # TODO
        #if step_f > TTFB_POOR_TIME_THRESHOLD:
        #    points -= TTFB_POOR_POINT_DEDUCTION

        #if step_ttfb > TTFB_NEEDS_IMPROVEMENT_TIME_THRESHOLD:
        #    points -= TTFB_NEEDS_IMPROVEMENT_POINT_DEDUCTION
        
        # Cert days remaining
        # Only check is page isn't insecure (in which case we've already dinged and know that this check is unneccessary)
        if not is_insecure and cert_days_remaining < CERT_DAYS_REMAINING_THRESHOLD:
            score_reduction_reasons.append(f"Removing {CERT_INVALIDATING_SOON_POINT_DEDUCTION} points from {step_name} because cert days remaining ({cert_days_remaining}) < {CERT_DAYS_REMAINING_THRESHOLD}")
            print(f"Removing {CERT_INVALIDATING_SOON_POINT_DEDUCTION} points from {step_name} because cert days remaining ({cert_days_remaining}) < {CERT_DAYS_REMAINING_THRESHOLD}")
            points -= CERT_INVALIDATING_SOON_POINT_DEDUCTION

        # Can"t have negative points
        if points < 0: points = 0
        

        print(f"Endpoint: {step_name} Points: {points}")

        #print(f"{step['requestName']} had status: {status} ({step_response_status_code}) and was {step['healthStatus']}")
        results.append({
            "url": f"{step['requestName']}",
            "score": points,
            "reasons": score_reduction_reasons
        })
        print(f"")
    print("-----")

# Build nicely formatted output table for PR comment
table_content = "<table><tr><th>Status</th><th>URL</th><th>Score</th><th>Score Reduction Reasons</th>"
for result in results:
    score = result['score']
    status = ":white_check_mark:" # default to a green tick (all OK)
    if score < FAIL_THRESHOLD:
        status = ":x:"
    elif score < WARNING_THRESHOLD:
        status = ":warning:"
    table_content += f"<tr><td>{status}</td><td>{result['url']}</td><td>{result['score']}%</td><td>{result['reasons']}</td></tr>"
table_content += "</table>"

# Set variable so other GitHub Actions can use the variable
# This line is important
print(f"::set-output name=table_content::{table_content}")
