import { AppShell } from "@/components/AppShell";
import { LiveProvider } from "@/lib/LiveContext";

export default function DeskLayout({ children }: { children: React.ReactNode }) {
  return (
    <LiveProvider>
      <AppShell>{children}</AppShell>
    </LiveProvider>
  );
}
