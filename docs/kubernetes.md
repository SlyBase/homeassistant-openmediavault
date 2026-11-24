# Kubernetes workloads: design notes

This page collects the background for the Kubernetes feature that the README only summarizes. It covers how the Recreate pods button behaves, what the feature costs on the NAS, why it is built the way it is, and which plugin changes would make finer control possible. The user-facing description lives in the [README](../README.md#kubernetes-workload-devices-one-device-per-workload-requires-openmediavault-k8s).

## How the Recreate pods button works

The button sends one `Kubectl.delete` request for each pod of the workload. A Deployment, StatefulSet or DaemonSet always replaces a pod that was deleted, so a new pod starts right away. The OMV documentation recommends the same move for updating an app: "In most cases, it is sufficient to first delete the image and then the pod of the application to be updated" ([OMV docs](https://docs.openmediavault.org/en/stable/plugins/available/k8s.html)).

Keep in mind that all pods of the workload are deleted at the same time. A workload with a single replica is offline for a few seconds while its new pod starts.

On the NAS, `kubectl delete` waits until the old pod has completely stopped. A pod may take as long as its `terminationGracePeriodSeconds` to stop, and some apps set that to a minute or more. The integration does not wait that long. It stops waiting after 10 seconds per pod and only writes a debug line to the log. This is safe because the delete is not cancelled when the caller stops waiting. In a test on OMV 8 with openmediavault-k8s 8.2.9-1, the caller was stopped 5 seconds into the delete of a pod with a 45 second grace period. The `kubectl delete` command was still listed in the omv-engined process list after that. The old pod still finished terminating, and its replacement was running 7 seconds later.

The integration only counts a delete as requested when OMV received the request and then did not answer within those 10 seconds. If the connection to OMV cannot even be opened, nothing was sent, so the button reports an error instead of pretending that it worked.

While the button is working, other requests to OMV have to wait, and that includes the next regular update. The 10 second limit keeps that delay short.

You will not find a button to stop, start or scale a workload. The plugin has no RPC that does that, and its only write method is `Kubectl.apply`, which expects a full manifest. Sending a partial manifest could wipe settings from apps that were installed with a recipe or with Helm, so this integration does not use it.

## How much load this puts on the NAS

Each update makes two extra RPC calls. The first one asks for workloads and pods together, and the second one asks for the pod metrics. The integration also needs to know how much CPU the node has, but it asks for that only about once an hour. On a test system (OMV 8 on an Intel i5-4690T with 32 workloads and 36 pods) the two calls took about 0.26 and 0.11 seconds. Nothing new gets installed in the cluster. The metrics come from the metrics-server that k3s already runs, which used about 8 millicores of CPU and 25 MiB of memory on that system.

## Why it is built this way

These are the decisions behind the feature, together with where they come from. The OMV forum was offline while this was written ([openmediavault#2301](https://github.com/openmediavault/openmediavault/issues/2301)), so the quotes come from GitHub and from the official documentation.

- The feature talks to OMV and to nothing else. Everything in this integration goes through OMV's JSON-RPC API, and plugin features are read with `_fetch_optional` so that they disappear cleanly when the plugin is not installed. That rule is spelled out in the [triage comment on #158](https://github.com/SlyBase/homeassistant-openmediavault/issues/158#issuecomment-7082930252). The openmediavault-k8s plugin happens to ship a `Kubectl` RPC service, and its `getList` method returns any Kubernetes resource as JSON. Because of that, you need no kubeconfig, no service account token and no extra port.
- The feature stays small on purpose. The OMV maintainer describes the plugin as "a single-node Kubernetes environment using K3s; nothing more" ([openmediavault#1722](https://github.com/openmediavault/openmediavault/pull/1722)). He also wrote: "The plugin UI was always intended solely to provide a quick overview. Everything else was meant to be done using external tools." ([openmediavault#2163](https://github.com/openmediavault/openmediavault/issues/2163)). The OMV docs say that OMV "offers a rudimentary UI for managing simple tasks in the cluster" and point to tools like Rancher or k9s for the rest. Following that idea, this feature shows the few numbers you would want on a dashboard and adds one safe action. It does not try to become a Kubernetes management tool.
- There is no monitoring stack to install. A Prometheus setup would give you much more data, but it costs a lot of memory for a NAS. The kube-state-metrics README alone recommends "250MiB memory" and "0.1 cores" before you even add Prometheus ([kube-state-metrics](https://github.com/kubernetes/kube-state-metrics)). OMV does not offer a recipe for it, and the idea of a monitoring plugin is still an open backlog item ([openmediavault#1833](https://github.com/openmediavault/openmediavault/issues/1833)). Glances does not help here either, because it cannot see containerd containers yet ([nicolargo/glances#2226](https://github.com/nicolargo/glances/issues/2226)).
- The controls follow the VM feature. When VM support was added, the destructive `force` command was "intentionally not exposed" (see the 2.5.0 entry in the CHANGELOG). The Recreate pods button follows the same idea. Kubernetes is built to recover from deleted pods, but it cannot repair a half-applied manifest.

## Ideas for the openmediavault-k8s plugin

A few extra RPC methods in the plugin would allow much finer control while keeping the rule that everything goes through OMV. None of them exist today, so treat this list as suggestions for the [openmediavault](https://github.com/openmediavault/openmediavault) project.

- `Kubectl.scale` with type, name, namespace and replicas, running `kubectl scale`. With that method, Home Assistant could offer a real on/off switch for each workload. Scaling to 0 would stop an app, and scaling back to the previous count would start it again.
- `Kubectl.rolloutRestart` with type, name and namespace, running `kubectl rollout restart`. This replaces the pods one at a time and waits until each new pod is ready, so an app with several replicas stays online. It would make a better default than deleting all pods at once.
- A `wait` flag on `Kubectl.delete` that maps to `kubectl delete --wait=false`. The call would then return as soon as Kubernetes accepts the delete, and clients would not need a timeout to avoid blocking.
- Label and field selectors on `Kubectl.getList`. Right now the pod list returns every field of every pod, which is about 190 KB for 36 pods. With a selector, or with a way to ask for specific columns, a client could fetch only the fields it shows.
- An official way to read metrics, for example a `Kubectl.top` method. Asking `getList` for `pods.metrics.k8s.io` works today, but it relies on `getList` passing the type to kubectl unchanged, which is not a documented feature.
- A read-only permission for the list and stats methods. Every Kubectl method currently requires the OMV administrator role, so Home Assistant needs an admin account just to read numbers.
- Suspend and resume for CronJobs, for example by patching `spec.suspend`. That would allow a switch that pauses a scheduled job.

