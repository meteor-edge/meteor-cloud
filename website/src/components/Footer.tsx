import Link from "next/link";

export function Footer() {
  return (
    <footer className="mt-16 border-t border-border bg-card">
      <div className="mx-auto grid max-w-6xl gap-8 px-4 py-12 sm:grid-cols-3">
        <div>
          <p className="font-semibold">MeteorCloud</p>
          <p className="mt-2 text-sm text-muted">Self-hosted Linux fleet control. You run the servers.</p>
        </div>
        <div>
          <p className="text-sm font-semibold">Product</p>
          <ul className="mt-2 space-y-1 text-sm text-muted">
            <li>
              <Link href="/docs" className="hover:text-foreground">
                Documentation
              </Link>
            </li>
            <li>
              <Link href="/docs/getting-started" className="hover:text-foreground">
                Get started
              </Link>
            </li>
            <li>
              <Link href="/docs/api" className="hover:text-foreground">
                API
              </Link>
            </li>
          </ul>
        </div>
        <div>
          <p className="text-sm font-semibold">Company</p>
          <ul className="mt-2 space-y-1 text-sm text-muted">
            <li>
              <Link href="/about" className="hover:text-foreground">
                About
              </Link>
            </li>
            <li>
              <Link href="/contact" className="hover:text-foreground">
                Contact
              </Link>
            </li>
          </ul>
        </div>
      </div>
      <p className="border-t border-border py-4 text-center text-xs text-muted">
        © {new Date().getFullYear()} MeteorCloud
      </p>
    </footer>
  );
}
