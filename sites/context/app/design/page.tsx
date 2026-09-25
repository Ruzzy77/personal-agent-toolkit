import {redirect} from 'next/navigation';
import {requireOwnerUser} from '@/lib/owner-service';

export const dynamic = 'force-dynamic';
export default async function DesignPage(){
 await requireOwnerUser('/design');
 redirect('https://personal-uikit.hiyaq77.workers.dev/');
}
