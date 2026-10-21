"use client";

import { useState } from "react";
import CallPanel from "@/components/CallPanel";
import SimPanel from "@/components/SimPanel";

export default function Home() {
  const [tab, setTab] = useState<"call" | "sim">("call");
  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          Client Simulator
        </div>
        <nav className="tabs" role="tablist" aria-label="Sections">
          <button role="tab" aria-selected={tab === "call"} onClick={() => setTab("call")}>
            Live call
          </button>
          <button role="tab" aria-selected={tab === "sim"} onClick={() => setTab("sim")}>
            Call center simulation
          </button>
        </nav>
      </header>
      <main>
        {/* both stay mounted so a call in progress survives a tab switch */}
        <div hidden={tab !== "call"}>
          <CallPanel />
        </div>
        <div hidden={tab !== "sim"}>
          <SimPanel />
        </div>
      </main>
    </>
  );
}
