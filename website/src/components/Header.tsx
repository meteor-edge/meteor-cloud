import Image from "next/image";
import Link from "next/link";

const links = [
  { href: "/docs", label: "Docs" },
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
];

export function Header() {
  return (
    <header className="sticky top-0 z-40 border-b border-border/80 bg-background/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
        <Link href="/" className="flex items-center gap-2">
          <Image src="/brand/icon.png" alt="" width={32} height={32} />
          <span className="text-lg font-semibold tracking-tight">MeteorCloud</span>
        </Link>
        <nav className="flex items-center gap-6 text-sm font-medium text-muted">
          {links.map((link) => (
            <Link key={link.href} href={link.href} className="hover:text-foreground">
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
