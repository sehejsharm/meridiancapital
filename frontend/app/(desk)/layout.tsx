import { AppShell } from "@/components/AppShell";
import { SecuritySetupNotice } from "@/components/SecuritySetupNotice";
import { LiveProvider } from "@/lib/LiveContext";

export default function DeskLayout({ children }: { children: React.ReactNode }) {
  return (
    <LiveProvider>
      <AppShell>
        <SecuritySetupNotice />
        {children}
      </AppShell>
    </LiveProvider>
  );
}
