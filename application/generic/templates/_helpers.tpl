{{- define "web-service.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "web-service.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "web-service.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- define "web-service.selectorLabels" -}}
app.kubernetes.io/name: {{ include "web-service.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "web-service.labels" -}}
{{ include "web-service.selectorLabels" . }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "web-service.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "web-service.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- required "serviceAccount.name is required when create=false" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
{{- define "web-service.image" -}}
{{- $repo := required "image.repository is required" .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" $repo (required "image.tag or image.digest is required" .Values.image.tag) -}}
{{- end -}}
{{- end -}}
{{- define "web-service.validate" -}}
{{- if and .Values.autoscaling.enabled (gt (int .Values.autoscaling.minReplicas) (int .Values.autoscaling.maxReplicas)) -}}
{{- fail "autoscaling.minReplicas must not exceed maxReplicas" -}}
{{- end -}}
{{- if and .Values.autoscaling.enabled (not .Values.resources.requests.cpu) -}}
{{- fail "CPU autoscaling requires resources.requests.cpu" -}}
{{- end -}}
{{- if and (ne .Values.exposure.type "none") (not .Values.exposure.host) -}}
{{- fail "exposure.host is required for ingress or route" -}}
{{- end -}}
{{- if and (eq .Values.exposure.type "route") (not (.Capabilities.APIVersions.Has "route.openshift.io/v1")) -}}
{{- fail "OpenShift Route API unavailable; for offline rendering use --api-versions route.openshift.io/v1" -}}
{{- end -}}
{{- end -}}
