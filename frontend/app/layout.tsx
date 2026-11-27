import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "APEX GOLD AI",
  description: "Plateforme de trading XAU/USD — gestion du risque d'abord",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
