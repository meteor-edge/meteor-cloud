/* Mosquitto plugin entry points (plugin API v5). All logic is in Go (authz). */
#include <mosquitto.h>
#include <mosquitto_broker.h>
#include <mosquitto_plugin.h>

#include "_cgo_export.h"

static mosquitto_plugin_id_t *mc_plugin_id = NULL;

static int mc_on_basic_auth(int event, void *event_data, void *userdata)
{
	struct mosquitto_evt_basic_auth *ed = event_data;
	(void)event;
	(void)userdata;
	return mcBasicAuth((char *)mosquitto_client_id(ed->client), ed->username, ed->password);
}

static int mc_on_acl_check(int event, void *event_data, void *userdata)
{
	struct mosquitto_evt_acl_check *ed = event_data;
	(void)event;
	(void)userdata;
	return mcAclCheck((char *)mosquitto_client_id(ed->client), (char *)mosquitto_client_username(ed->client),
			ed->access, (char *)ed->topic, ed->retain ? 1 : 0);
}

static int mc_on_message(int event, void *event_data, void *userdata)
{
	struct mosquitto_evt_message *ed = event_data;
	char *target;
	(void)event;
	(void)userdata;
	target = mcKickTarget((char *)mosquitto_client_username(ed->client), ed->topic, ed->payload, (int)ed->payloadlen);
	if(target){
		mosquitto_kick_client_by_username(target, false);
		mosquitto_log_printf(MOSQ_LOG_NOTICE, "meteorcloud-auth: disconnected sessions of %s", target);
		free(target);
	}
	return MOSQ_ERR_SUCCESS;
}

void mc_log(int level, char *message)
{
	mosquitto_log_printf(level, "meteorcloud-auth: %s", message);
}

int mosquitto_plugin_version(int supported_version_count, const int *supported_versions)
{
	int i;
	for(i = 0; i < supported_version_count; i++){
		if(supported_versions[i] == 5){
			return 5;
		}
	}
	return -1;
}

int mosquitto_plugin_init(mosquitto_plugin_id_t *identifier, void **userdata, struct mosquitto_opt *options, int option_count)
{
	int i, rc;
	(void)userdata;

	for(i = 0; i < option_count; i++){
		mcSetOption(options[i].key, options[i].value);
	}
	if(mcStart() != 0){
		return MOSQ_ERR_INVAL; /* the broker refuses to start: never run without auth */
	}
	mc_plugin_id = identifier;
	rc = mosquitto_callback_register(identifier, MOSQ_EVT_BASIC_AUTH, mc_on_basic_auth, NULL, NULL);
	if(rc) return rc;
	rc = mosquitto_callback_register(identifier, MOSQ_EVT_ACL_CHECK, mc_on_acl_check, NULL, NULL);
	if(rc) return rc;
	return mosquitto_callback_register(identifier, MOSQ_EVT_MESSAGE, mc_on_message, NULL, NULL);
}

int mosquitto_plugin_cleanup(void *userdata, struct mosquitto_opt *options, int option_count)
{
	(void)userdata;
	(void)options;
	(void)option_count;
	if(mc_plugin_id){
		mosquitto_callback_unregister(mc_plugin_id, MOSQ_EVT_BASIC_AUTH, mc_on_basic_auth, NULL);
		mosquitto_callback_unregister(mc_plugin_id, MOSQ_EVT_ACL_CHECK, mc_on_acl_check, NULL);
		mosquitto_callback_unregister(mc_plugin_id, MOSQ_EVT_MESSAGE, mc_on_message, NULL);
	}
	return MOSQ_ERR_SUCCESS;
}
