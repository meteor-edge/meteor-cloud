// Command mosquitto-auth is the MeteorCloud auth plugin for Mosquitto.
//
// Build:  go build -buildmode=c-shared -o meteorcloud_auth.so .
// Load:   plugin /usr/lib/mosquitto/meteorcloud_auth.so   (mosquitto.conf)
//
// Settings come from the environment first, then from plugin_opt_* lines:
//
//	CONTROL_PLANE_URL     / plugin_opt_control_plane_url    (required)
//	MQTT_INTERNAL_TOKEN   / plugin_opt_internal_token       (required; prefer the env var)
//	MQTT_AUTH_TIMEOUT_MS  / plugin_opt_timeout_ms           (default 3000)
//	MQTT_ACL_CACHE_SECONDS / plugin_opt_acl_cache_seconds   (default 60; 0 disables)
//	MQTT_ACL_DENY_CACHE_SECONDS / plugin_opt_acl_deny_cache_seconds (default 10)
//
// The plugin holds no rules. See package authz.
package main

/*
#cgo LDFLAGS: -Wl,--unresolved-symbols=ignore-all
#include <stdlib.h>

void mc_log(int level, char *message);
*/
import "C"

import (
	"os"
	"strconv"
	"sync"
	"time"
	"unsafe"

	"meteorcloud/mosquitto-auth/authz"
)

// Mosquitto return codes and log levels (mosquitto.h); kept local so this file
// does not include the broker headers twice.
const (
	mosqErrSuccess = 0
	mosqErrAuth    = 11
	mosqErrACLDeny = 12

	mosqLogInfo    = 0x01
	mosqLogWarning = 0x04
	mosqLogErr     = 0x08
)

var (
	mu      sync.Mutex
	options = map[string]string{}
	engine  *authz.Engine
)

func logf(level int, message string) {
	cs := C.CString(message)
	defer C.free(unsafe.Pointer(cs))
	C.mc_log(C.int(level), cs)
}

func setting(env, opt, fallback string) string {
	if value := os.Getenv(env); value != "" {
		return value
	}
	if value := options[opt]; value != "" {
		return value
	}
	return fallback
}

func seconds(env, opt string, fallback int) time.Duration {
	n, err := strconv.Atoi(setting(env, opt, strconv.Itoa(fallback)))
	if err != nil || n < 0 {
		n = fallback
	}
	return time.Duration(n) * time.Second
}

//export mcSetOption
func mcSetOption(key, value *C.char) {
	mu.Lock()
	defer mu.Unlock()
	options[C.GoString(key)] = C.GoString(value)
}

//export mcStart
func mcStart() C.int {
	mu.Lock()
	defer mu.Unlock()
	timeoutMs, err := strconv.Atoi(setting("MQTT_AUTH_TIMEOUT_MS", "timeout_ms", "3000"))
	if err != nil || timeoutMs <= 0 {
		timeoutMs = 3000
	}
	built, err := authz.New(authz.Config{
		ControlPlaneURL: setting("CONTROL_PLANE_URL", "control_plane_url", ""),
		InternalToken:   setting("MQTT_INTERNAL_TOKEN", "internal_token", ""),
		Timeout:         time.Duration(timeoutMs) * time.Millisecond,
		AllowTTL:        seconds("MQTT_ACL_CACHE_SECONDS", "acl_cache_seconds", 60),
		DenyTTL:         seconds("MQTT_ACL_DENY_CACHE_SECONDS", "acl_deny_cache_seconds", 10),
	})
	if err != nil {
		logf(mosqLogErr, "cannot start: "+err.Error())
		return 1
	}
	engine = built
	logf(mosqLogInfo, "ready, control plane "+setting("CONTROL_PLANE_URL", "control_plane_url", ""))
	return 0
}

//export mcBasicAuth
func mcBasicAuth(clientID, username, password *C.char) C.int {
	if engine == nil || username == nil {
		return mosqErrAuth
	}
	ok, err := engine.Authenticate(C.GoString(clientID), C.GoString(username), C.GoString(password))
	if err != nil {
		logf(mosqLogWarning, "login check failed, denying: "+err.Error())
	}
	if ok {
		return mosqErrSuccess
	}
	return mosqErrAuth
}

//export mcAclCheck
func mcAclCheck(clientID, username *C.char, access C.int, topic *C.char, retain C.int) C.int {
	if engine == nil || username == nil || topic == nil {
		return mosqErrACLDeny
	}
	ok, err := engine.Authorize(C.GoString(clientID), C.GoString(username), int(access), C.GoString(topic), retain != 0)
	if err != nil {
		logf(mosqLogWarning, "topic check failed, denying: "+err.Error())
	}
	if ok {
		return mosqErrSuccess
	}
	return mosqErrACLDeny
}

// mcKickTarget returns a malloc'd username to disconnect, or NULL. The caller frees it.
//
//export mcKickTarget
func mcKickTarget(username, topic *C.char, payload unsafe.Pointer, payloadLen C.int) *C.char {
	if engine == nil || username == nil || topic == nil {
		return nil
	}
	var body []byte
	if payload != nil && payloadLen > 0 {
		body = C.GoBytes(payload, payloadLen)
	}
	target, ok := engine.KickTarget(C.GoString(username), C.GoString(topic), body)
	if !ok {
		return nil
	}
	return C.CString(target)
}

func main() {}
