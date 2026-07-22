export interface CurrentUser {
  username: string;
  role: 'user' | 'admin';
  tenant_id?: string;
  patient_id?: string;
}
