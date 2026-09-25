import { FlowWorkspace } from '@/components/flow/flow-workspace';
import { requireOwnerUser } from '@/lib/owner-service';
export const dynamic = 'force-dynamic';
export default async function FlowPage() {
  await requireOwnerUser('/flow');
  return <FlowWorkspace />;
}
