/**
 * Shown at the top of every desk page while this deployment has no relay secret.
 * A server component: it reads the setting here and sends only the warning.
 *
 * Without the secret the control plane answers anyone on the internet (its
 * sign-in route included) and the session cookie cannot be sealed, so the door in
 * proxy.ts can check only its shape. Local development and CI never show it.
 */
export function SecuritySetupNotice() {
  if (process.env.VERCEL !== "1" || process.env.MERIDIAN_RELAY_SECRET?.trim()) return null;
  return (
    <div role="alert" className="rounded-lg border border-warning/50 bg-warning/10 px-4 py-3 text-xs leading-relaxed text-warning">
      <strong className="font-semibold">Security setup is not finished.</strong> <code>MERIDIAN_RELAY_SECRET</code> is
      not set on this deployment. Until the same value is set here (Vercel, Environment Variables) and in{" "}
      <code>/etc/meridian/meridian.env</code> on the server, the control plane answers anyone on the internet and the
      session cookie is not sealed. DEPLOYMENT.md, “The relay secret”, has the three commands.
    </div>
  );
}
