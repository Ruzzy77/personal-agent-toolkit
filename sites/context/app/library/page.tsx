import { App } from '@/components/library/library-app';
import { requireOwnerUser } from '@/lib/owner-service';

export const dynamic = 'force-dynamic';

export default async function LibraryPage() {
  await requireOwnerUser('/library');
  return <App />;
}
