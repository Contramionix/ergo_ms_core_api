<template>
  <form>
    <FormField
      :label="t('demo_mod.create.nameLabel')"
      :hint="t('demo_mod.create.nameHint')"
      required
    >
      <input type="text" />
    </FormField>
    <FormField :label="t('demo_mod.create.noteLabel')" optional>
      <input type="text" placeholder="необязательная заметка" />
    </FormField>
    <FormField label="Литерал без перевода">
      <input required type="text" />
    </FormField>
    <button type="submit" class="ui-btn ui-btn--primary">
      {{ t('demo_mod.create.save') }}
    </button>
    <button type="button">{{ t('demo_mod.create.cancel') }}</button>
  </form>
</template>

<script setup>
import FormField from '@/components/FormField.vue'
</script>
