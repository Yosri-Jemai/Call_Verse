import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Relay | Agent Communication Studio",
  description: "A live conversation between the client simulator and the banking advisor.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
