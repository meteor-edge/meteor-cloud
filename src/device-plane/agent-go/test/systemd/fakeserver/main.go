// Command fakeserver is a test tool for test/systemd/run.sh: a tiny control
// plane on 127.0.0.1:8099 that enrolls the device and accepts heartbeats, so
// the container test can check register and approval end to end under real
// systemd. An enrollment request stays pending until the file named by
// -approve exists (an operator approving in the console). Every request is
// appended to -log as "<unix ms> <METHOD> <path> auth=<yes|no> name=<name>";
// secrets are never written.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"sync"
	"time"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:8099", "listen address")
	logPath := flag.String("log", "/var/log/fakeserver.log", "request log")
	approve := flag.String("approve", "/var/lib/fakeserver/approve", "requests are approved once this file exists")
	flag.Parse()

	out, err := os.OpenFile(*logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		log.Fatal(err)
	}
	var mu sync.Mutex
	record := func(r *http.Request, name string) {
		mu.Lock()
		defer mu.Unlock()
		auth := "no"
		if r.Header.Get("Authorization") != "" {
			auth = "yes"
		}
		fmt.Fprintf(out, "%d %s %s auth=%s name=%s\n", time.Now().UnixMilli(), r.Method, r.URL.Path, auth, name)
	}
	reply := func(w http.ResponseWriter, status int, body any) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(body)
	}
	enrollment := map[string]any{
		"device_id": "11111111-2222-4333-8444-555555555555", "device_token": "dev_systemdtestdevicetoken",
		"organization_id": "66666666-7777-4888-9999-000000000000", "name": "edge-test",
		"heartbeat_interval_seconds": 60, "mqtt": nil,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/v1/agent/register", func(w http.ResponseWriter, r *http.Request) {
		var body struct{ Name string }
		_ = json.NewDecoder(r.Body).Decode(&body)
		record(r, body.Name)
		reply(w, http.StatusCreated, enrollment)
	})
	mux.HandleFunc("POST /api/v1/agent/enroll/request", func(w http.ResponseWriter, r *http.Request) {
		var body struct{ Name string }
		_ = json.NewDecoder(r.Body).Decode(&body)
		record(r, body.Name)
		reply(w, http.StatusCreated, map[string]any{"request_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
			"claim_secret": "clm_systemdtestclaimsecret", "status": "pending", "poll_interval_seconds": 10})
	})
	mux.HandleFunc("POST /api/v1/agent/enroll/poll", func(w http.ResponseWriter, r *http.Request) {
		record(r, "")
		if _, err := os.Stat(*approve); err != nil {
			reply(w, http.StatusOK, map[string]any{"status": "pending", "poll_interval_seconds": 10})
			return
		}
		approved := map[string]any{"status": "approved", "poll_interval_seconds": 10}
		for k, v := range enrollment {
			approved[k] = v
		}
		reply(w, http.StatusOK, approved)
	})
	mux.HandleFunc("POST /api/v1/agent/heartbeat", func(w http.ResponseWriter, r *http.Request) {
		record(r, "")
		reply(w, http.StatusOK, map[string]any{"device_id": enrollment["device_id"], "status": "online",
			"heartbeat_interval_seconds": 60, "server_time": time.Now().UTC().Format(time.RFC3339),
			"desired_software": []any{}, "wifi_pending": false, "actions_pending": false})
	})
	mux.HandleFunc("GET /api/v1/agent/mqtt", func(w http.ResponseWriter, r *http.Request) {
		record(r, "")
		reply(w, http.StatusOK, map[string]any{"enabled": false, "config_version": nil})
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		record(r, "")
		reply(w, http.StatusNotFound, map[string]any{"error": map[string]string{"code": "not_found", "message": "not scripted"}})
	})
	srv := &http.Server{Addr: *addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	log.Fatal(srv.ListenAndServe())
}
