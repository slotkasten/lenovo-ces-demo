{{/* Expand the name of the chart. */}}
{{- define "northwind.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Fully qualified app name. */}}
{{- define "northwind.fullname" -}}
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

{{- define "northwind.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "northwind.labels" -}}
helm.sh/chart: {{ include "northwind.chart" . }}
{{ include "northwind.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: northwind-bank
{{- end }}

{{- define "northwind.selectorLabels" -}}
app.kubernetes.io/name: {{ include "northwind.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "northwind.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "northwind.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Web image reference. */}}
{{- define "northwind.image" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- printf "%s/%s:%s" .Values.image.registry .Values.image.repository $tag -}}
{{- end }}

{{/* PostgreSQL service / host. */}}
{{- define "northwind.postgresql.fullname" -}}
{{- printf "%s-postgresql" (include "northwind.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "northwind.postgresql.host" -}}
{{- if .Values.postgresql.enabled -}}
{{ include "northwind.postgresql.fullname" . }}
{{- else -}}
{{ .Values.external.host }}
{{- end -}}
{{- end }}

{{- define "northwind.postgresql.port" -}}
{{- if .Values.postgresql.enabled -}}{{ .Values.postgresql.service.port }}{{- else -}}{{ .Values.external.port }}{{- end -}}
{{- end }}

{{- define "northwind.postgresql.database" -}}
{{- if .Values.postgresql.enabled -}}{{ .Values.postgresql.auth.database }}{{- else -}}{{ .Values.external.database }}{{- end -}}
{{- end }}

{{- define "northwind.postgresql.username" -}}
{{- if .Values.postgresql.enabled -}}{{ .Values.postgresql.auth.username }}{{- else -}}{{ .Values.external.username }}{{- end -}}
{{- end }}

{{/* Name of the Secret holding the DB password. */}}
{{- define "northwind.postgresql.secretName" -}}
{{- if .Values.postgresql.enabled -}}
{{- if .Values.postgresql.auth.existingSecret -}}{{ .Values.postgresql.auth.existingSecret }}{{- else -}}{{ include "northwind.postgresql.fullname" . }}{{- end -}}
{{- else -}}
{{ .Values.external.existingSecret }}
{{- end -}}
{{- end }}

{{- define "northwind.postgresql.secretKey" -}}
{{- if .Values.postgresql.enabled -}}{{ .Values.postgresql.auth.secretKeys.passwordKey }}{{- else -}}{{ .Values.external.secretKeys.passwordKey }}{{- end -}}
{{- end }}

{{/* Ollama service name/host. */}}
{{- define "northwind.ollama.fullname" -}}
{{- printf "%s-ollama" (include "northwind.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "northwind.ollama.url" -}}
{{- printf "http://%s:11434" (include "northwind.ollama.fullname" .) -}}
{{- end }}

{{/* Common env block for containers that talk to the DB and models. */}}
{{- define "northwind.commonEnv" -}}
- name: PGHOST
  value: {{ include "northwind.postgresql.host" . | quote }}
- name: PGPORT
  value: {{ include "northwind.postgresql.port" . | quote }}
- name: PGDATABASE
  value: {{ include "northwind.postgresql.database" . | quote }}
- name: PGUSER
  value: {{ include "northwind.postgresql.username" . | quote }}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "northwind.postgresql.secretName" . | quote }}
      key: {{ include "northwind.postgresql.secretKey" . | quote }}
- name: EMBED_PROVIDER
  value: {{ .Values.embedProvider | quote }}
- name: EMBED_DIM
  value: {{ .Values.embedDim | quote }}
- name: LLM_ENABLED
  value: {{ .Values.llm.enabled | quote }}
{{- if .Values.llm.enabled }}
- name: OLLAMA_URL
  value: {{ include "northwind.ollama.url" . | quote }}
- name: OLLAMA_CHAT_MODEL
  value: {{ .Values.llm.chatModel | quote }}
- name: OLLAMA_EMBED_MODEL
  value: {{ .Values.llm.embedModel | quote }}
{{- end }}
{{- end }}
