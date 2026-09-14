# Dashboard auf Kubernetes ausrollen

Diese Dateien setzen voraus, dass der MCP-Server bereits nach der
Haupt-`README.md` (Abschnitt "Kubernetes Deployment") eingerichtet ist:
Namespace `mcpo`, Secret `garmin-secrets`, PVC `garmin-tokens`.

## 1. Secrets anlegen

Dashboard-Login und (optional) Anthropic-Key:

```bash
kubectl create secret generic dashboard-secrets \
  --from-literal=username='<dein-dashboard-username>' \
  --from-literal=password='<ein-starkes-passwort>' \
  --from-literal=anthropic-api-key='<sk-ant-...>' \
  -n mcpo
```

Ohne KI-Trainingsberater einfach die `anthropic-api-key`-Zeile weglassen.

Pull-Secret, damit Kubernetes das private Image von ghcr.io herunterladen darf:

```bash
kubectl create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username='<dein-github-username>' \
  --docker-password='<github-token-mit-read:packages>' \
  --docker-email='<deine-email>' \
  -n mcpo
```

## 2. Ausrollen

```bash
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

## 3. Prüfen

```bash
kubectl get pods -n mcpo -l app=garmin-dashboard
kubectl logs -n mcpo deployment/garmin-dashboard -f
```

Sobald der Pod `Running`/`Ready` ist, ist das Dashboard clusterintern unter
`http://garmin-dashboard.mcpo.svc.cluster.local` erreichbar. Wie du von
außerhalb (aber weiterhin privat, z. B. über dein Tailnet) heran kommst,
hängt von eurem bestehenden Setup ab - siehe Haupt-README.
