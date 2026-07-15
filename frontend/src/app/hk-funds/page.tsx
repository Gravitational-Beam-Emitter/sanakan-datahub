import NavBar from "@/components/NavBar";
import ThemeToggle from "@/components/ThemeToggle";
import HkFundsContent from "./HkFundsContent";

export default function HkFundsPage() {
  return (
    <div className="min-h-screen bg-canvas text-ink">
      <div className="max-w-7xl mx-auto px-4 py-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">HK 基金尽调 KYP</h1>
            <p className="text-sm text-muted mt-1">
              SFC 认可基金 + 复杂产品分类 + 管理人尽调
            </p>
          </div>
          <div className="flex items-center gap-3">
            <ThemeToggle />
          </div>
        </div>

        <NavBar />

        <div className="mt-6">
          <HkFundsContent />
        </div>

        {/* Footer */}
        <footer className="text-center mt-12 py-6 text-xs text-muted">
          HK Fund KYP Module — Data from SFC Public Register
        </footer>
      </div>
    </div>
  );
}
