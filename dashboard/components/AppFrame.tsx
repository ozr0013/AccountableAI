"use client";

import { usePathname } from "next/navigation";
import { FeedProvider } from "./Feed";
import { Shell } from "./Shell";
import { WorkspaceProvider } from "./workspace/Workspace";

export function AppFrame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  if (path === "/") return children;
  return <FeedProvider><WorkspaceProvider><Shell>{children}</Shell></WorkspaceProvider></FeedProvider>;
}
