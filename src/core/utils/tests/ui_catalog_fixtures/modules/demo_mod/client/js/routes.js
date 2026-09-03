export default {
  DemoCreate: {
    path: '/demo/create',
    name: 'DemoCreate',
    component: '@/modules/demo_mod/client/pages/CreateItem.vue',
    meta: {
      title: 'Создание записи',
      titleKey: 'demo_mod.routes.create',
      requiresAuth: true,
    },
  },
}
