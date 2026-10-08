{{- define "meteorcloud.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "meteorcloud.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "meteorcloud.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "meteorcloud.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Usage: include "meteorcloud.selectorLabels" (dict "ctx" $ "component" "backend") */}}
{{- define "meteorcloud.selectorLabels" -}}
app.kubernetes.io/name: {{ include "meteorcloud.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "meteorcloud.componentLabels" -}}
{{ include "meteorcloud.labels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/* Usage: include "meteorcloud.image" (dict "ctx" $ "image" .Values.backend.image) */}}
{{- define "meteorcloud.image" -}}
{{- printf "%s:%s" .image.repository (.image.tag | default .ctx.Chart.AppVersion | toString) }}
{{- end }}

{{- define "meteorcloud.secretName" -}}
{{- .Values.secrets.existingSecret | default (include "meteorcloud.fullname" .) }}
{{- end }}

{{- define "meteorcloud.mqttBrokerHost" -}}
{{- if eq .Values.mqtt.broker "bundled" }}
{{- printf "%s-emqx" (include "meteorcloud.fullname" .) }}
{{- else }}
{{- .Values.mqtt.external.host }}
{{- end }}
{{- end }}

{{- define "meteorcloud.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- .Values.serviceAccount.name | default (include "meteorcloud.fullname" .) }}
{{- else }}
{{- .Values.serviceAccount.name | default "default" }}
{{- end }}
{{- end }}

{{- define "meteorcloud.artifactsClaimName" -}}
{{- .Values.objectStorage.filesystem.existingClaim | default (printf "%s-artifacts" (include "meteorcloud.fullname" .)) }}
{{- end }}

{{- define "meteorcloud.filesystemRWO" -}}
{{- if and (eq .Values.objectStorage.provider "filesystem") (not (has "ReadWriteMany" .Values.objectStorage.filesystem.accessModes)) }}true{{ end }}
{{- end }}

{{/* Fails rendering on unsupported combinations; included from the ConfigMap. */}}
{{- define "meteorcloud.validate" -}}
{{- $v := .Values }}
{{- if not (has $v.postgresql.mode (list "bundled" "external")) }}
{{- fail "postgresql.mode must be bundled or external" }}
{{- end }}
{{- if and (eq $v.postgresql.mode "external") (not $v.secrets.existingSecret) (not $v.postgresql.external.url) (not $v.postgresql.external.host) }}
{{- fail "postgresql.mode=external needs postgresql.external.url, postgresql.external.host, or secrets.existingSecret with DATABASE_URL" }}
{{- end }}
{{- if not (has $v.cache.provider (list "memory" "redis")) }}
{{- fail "cache.provider must be memory or redis" }}
{{- end }}
{{- if and (eq $v.cache.provider "redis") (eq $v.cache.redis.mode "external") (not $v.secrets.existingSecret) (not $v.cache.redis.external.url) }}
{{- fail "cache.redis.mode=external needs cache.redis.external.url or secrets.existingSecret with REDIS_URL" }}
{{- end }}
{{- $redisUrl := $v.cache.redis.external.url | default "" }}
{{- if and (eq $v.cache.provider "redis") (eq $v.cache.redis.mode "external") $redisUrl (hasPrefix "redis://" $redisUrl) (contains "@" $redisUrl) }}
{{- fail "cache.redis.external.url with credentials must use rediss:// (TLS)" }}
{{- end }}
{{- if not (has $v.objectStorage.provider (list "filesystem" "s3")) }}
{{- fail "objectStorage.provider must be filesystem or s3" }}
{{- end }}
{{- if and (eq $v.objectStorage.provider "s3") (not $v.objectStorage.s3.bucket) }}
{{- fail "objectStorage.provider=s3 needs objectStorage.s3.bucket" }}
{{- end }}
{{- if and (include "meteorcloud.filesystemRWO" .) (gt (int $v.backend.replicaCount) 1) }}
{{- fail "backend.replicaCount > 1 needs objectStorage.provider=s3 or a ReadWriteMany filesystem volume" }}
{{- end }}
{{- if $v.mqtt.enabled }}
{{- if not (has $v.mqtt.broker (list "bundled" "external")) }}
{{- fail "mqtt.broker must be bundled or external" }}
{{- end }}
{{- if not $v.mqtt.tls.existingSecret }}
{{- fail "mqtt.enabled needs mqtt.tls.existingSecret (ca.crt; plus server.crt and server.key for the bundled broker)" }}
{{- end }}
{{- if and (eq $v.mqtt.broker "external") (not $v.mqtt.external.host) }}
{{- fail "mqtt.broker=external needs mqtt.external.host" }}
{{- end }}
{{- if not $v.mqtt.publicHost }}
{{- fail "mqtt.enabled needs mqtt.publicHost (the hostname devices connect to)" }}
{{- end }}
{{- end }}
{{- end }}
