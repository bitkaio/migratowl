{{- define "migratowl.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "migratowl.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "migratowl.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "migratowl.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "migratowl.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "migratowl.sandboxNamespace" -}}
{{- default .Release.Namespace .Values.sandbox.namespace -}}
{{- end -}}

{{- define "migratowl.routerUrl" -}}
{{- default (printf "http://sandbox-router-svc.%s.svc.cluster.local:8080" (include "migratowl.sandboxNamespace" .)) .Values.sandbox.routerUrl -}}
{{- end -}}
