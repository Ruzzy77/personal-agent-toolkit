import DesignGallery, { type Catalog } from '@/components/design/design-gallery';
import { designRequest } from '@/lib/design';
import { requireOwnerUser } from '@/lib/owner-service';

export const dynamic = 'force-dynamic';

export default async function DesignPage() {
  await requireOwnerUser('/design');
  const catalog = await designRequest<Catalog>('/api/v1/catalog');
  return <DesignGallery catalog={catalog} />;
}
