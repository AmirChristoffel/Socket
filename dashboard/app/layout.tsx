import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Smart City IoT Dashboard",
  description: "Painel de controle do sistema IoT distribuído",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body className="min-h-screen bg-gray-950 text-gray-100 antialiased">
        {children}
      </body>
    </html>
  );
}
