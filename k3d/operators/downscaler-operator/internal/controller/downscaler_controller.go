package controller

import (
    "context"
    "time"

    corev1 "k8s.io/api/core/v1"
    appsv1 "k8s.io/api/apps/v1"
    "k8s.io/apimachinery/pkg/api/errors"
    "k8s.io/apimachinery/pkg/runtime"
    "k8s.io/apimachinery/pkg/types"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
    "sigs.k8s.io/controller-runtime/pkg/log"
)

// RBAC permissions
//+kubebuilder:rbac:groups="",resources=pods,verbs=get;list;watch
//+kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;update;patch
//+kubebuilder:rbac:groups=apps,resources=replicasets,verbs=get;list;watch

type DownscalerReconciler struct {
    client.Client
    Scheme *runtime.Scheme
}

func (r *DownscalerReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    logger := log.FromContext(ctx)

    var pod corev1.Pod
    if err := r.Get(ctx, req.NamespacedName, &pod); err != nil {
        if errors.IsNotFound(err) {
            return ctrl.Result{}, nil
        }
        return ctrl.Result{}, err
    }

    // Skip kube-system and operator's own namespace
    if pod.Namespace == "kube-system" || pod.Namespace == "downscaler-system" {
        return ctrl.Result{}, nil
    }

    // If pod has been running for > 1m → downscale owner
    if pod.Status.StartTime != nil && time.Since(pod.Status.StartTime.Time) > time.Minute {
        for _, owner := range pod.OwnerReferences {
            if owner.Kind == "ReplicaSet" {
                var rs appsv1.ReplicaSet
                if err := r.Get(ctx, types.NamespacedName{Name: owner.Name, Namespace: pod.Namespace}, &rs); err == nil {
                    for _, rsOwner := range rs.OwnerReferences {
                        if rsOwner.Kind == "Deployment" {
                            var deploy appsv1.Deployment
                            if err := r.Get(ctx, types.NamespacedName{Name: rsOwner.Name, Namespace: pod.Namespace}, &deploy); err == nil {
                                replicas := int32(1)
                                deploy.Spec.Replicas = &replicas
                                if err := r.Update(ctx, &deploy); err != nil {
                                    return ctrl.Result{}, err
                                }
                                logger.Info("Scaled down deployment", deploy.Name)
                            }
                        }
                    }
                }
            }
        }
    }

    return ctrl.Result{RequeueAfter: time.Minute}, nil
}

func (r *DownscalerReconciler) SetupWithManager(mgr ctrl.Manager) error {
    return ctrl.NewControllerManagedBy(mgr).
        For(&corev1.Pod{}).
        Complete(r)
}
