package mqttfwd

import (
	"strings"
	"testing"
	"time"
)

func TestLoadConfigDefaultsMatchPythonService(t *testing.T) {
	t.Setenv("MQTT_INTERNAL_TOKEN", "tok")
	c, err := LoadConfig()
	if err != nil {
		t.Fatal(err)
	}
	if c.HTTPAddr != ":8081" || c.BrokerURL != "mqtts://localhost:8883" || c.Username != "platform" ||
		c.Provider != "mosquitto" || !c.MQTTEnabled || !c.MetricsEnabled || c.CACertPath != "certs/ca.crt" {
		t.Fatalf("defaults: %+v", c)
	}
	if c.ClientID != "meteorcloud-mqtt-forwarder" || c.SharedGroup != "meteorcloud-mqtt-forwarder" {
		t.Fatalf("client id / group: %+v", c)
	}
	if c.IngestTimeout != 10*time.Second {
		t.Fatalf("ingest timeout %v (Python used 10 s)", c.IngestTimeout)
	}
}

func TestLoadConfig(t *testing.T) {
	for _, tc := range []struct {
		name string
		env  map[string]string
		err  string
	}{
		{"broker host and port", map[string]string{"MQTT_BROKER_HOST": "mqtt-broker", "MQTT_BROKER_PORT": "8884"}, ""},
		{"url override", map[string]string{"MQTT_FORWARDER_BROKER_URL": "mqtt://127.0.0.1:1883"}, ""},
		{"booleans like pydantic", map[string]string{"MQTT_ENABLED": "OFF", "METRICS_ENABLED": "n"}, ""},
		{"empty shared group", map[string]string{"MQTT_FORWARDER_SHARED_GROUP": ""}, ""},
		{"bad boolean", map[string]string{"MQTT_ENABLED": "maybe"}, "not a boolean"},
		{"no token", map[string]string{"MQTT_INTERNAL_TOKEN": ""}, "MQTT_INTERNAL_TOKEN is required"},
		{"bad provider", map[string]string{"MQTT_PROVIDER": "hivemq"}, "unsupported MQTT_PROVIDER"},
		{"queue smaller than workers", map[string]string{"MQTT_FORWARDER_WORKERS": "8", "MQTT_FORWARDER_QUEUE_SIZE": "4"}, "QUEUE_SIZE"},
		{"queue over receive maximum", map[string]string{"MQTT_FORWARDER_QUEUE_SIZE": "70000"}, "QUEUE_SIZE"},
		{"bad int", map[string]string{"MQTT_FORWARDER_WORKERS": "x"}, "MQTT_FORWARDER_WORKERS"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("MQTT_INTERNAL_TOKEN", "tok")
			for k, v := range tc.env {
				t.Setenv(k, v)
			}
			c, err := LoadConfig()
			if tc.err == "" && err != nil {
				t.Fatal(err)
			}
			if tc.err != "" && (err == nil || !strings.Contains(err.Error(), tc.err)) {
				t.Fatalf("err = %v, want %q", err, tc.err)
			}
			if tc.name == "broker host and port" && c.BrokerURL != "mqtts://mqtt-broker:8884" {
				t.Fatalf("broker url %s", c.BrokerURL)
			}
			if tc.name == "empty shared group" && c.SharedGroup != "" {
				t.Fatalf("an empty MQTT_FORWARDER_SHARED_GROUP must mean a plain subscription, got %q", c.SharedGroup)
			}
			if tc.name == "booleans like pydantic" && (c.MQTTEnabled || c.MetricsEnabled) {
				t.Fatalf("booleans: %+v", c)
			}
		})
	}
}
