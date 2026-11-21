/* Feed sampled open-drain pads to libsigrokdecode's stock I2C decoder. */
#include <stdio.h>
#include <libsigrokdecode/libsigrokdecode.h>

static void annotation(struct srd_proto_data *item, void *unused)
{
    const struct srd_proto_data_annotation *ann = item->data;
    (void)unused;
    if (ann->ann_class != 5) /* Omit individual bit labels in version 0.5.3. */
        puts(ann->ann_text[0]);
}

static int replay(const char *path, const char *decoders)
{
    gchar *raw = NULL;
    gsize length = 0;
    GError *error = NULL;
    if (!g_file_get_contents(path, &raw, &length, &error)) {
        fprintf(stderr, "%s\n", error->message);
        g_error_free(error);
        return 1;
    }
    if (length == 0 || length % 3 != 0) {
        fprintf(stderr, "Expected nonempty three-byte samples.\n");
        g_free(raw);
        return 1;
    }

    guint8 *pads = (guint8 *)raw;
    gsize n = length / 3, rises = 0;
    for (gsize i = 0; i < n; i++) {
        guint8 out = pads[3*i], oe = pads[3*i + 1], target = pads[3*i + 2];
        if ((out & oe) || ((oe | target) & ~3)) {
            fprintf(stderr, "Invalid open-drain drive at sample %zu\n", i);
            g_free(raw);
            return 1;
        }
        guint8 levels = ~(oe | target) & 3;
        /* Capture bits 0/1 = SDA/SCL. Decoder channels 0/1 = SCL/SDA. */
        pads[i] = (levels >> 1) | ((levels & 1) << 1);
        if (i && !(pads[i-1] & 1) && (pads[i] & 1))
            rises++;
    }

    if (srd_init(decoders) != SRD_OK) {
        fprintf(stderr, "Could not initialize libsigrokdecode.\n");
        g_free(raw);
        return 1;
    }
    int rc = 1;
    struct srd_session *session = NULL;
    if (srd_decoder_load("i2c") != SRD_OK || srd_session_new(&session) != SRD_OK)
        goto done;

    GHashTable *options = g_hash_table_new(g_str_hash, g_str_equal);
    GVariant *shifted = g_variant_ref_sink(g_variant_new_string("shifted"));
    g_hash_table_insert(options, "address_format", shifted);
    struct srd_decoder_inst *decoder = srd_inst_new(session, "i2c", options);
    g_hash_table_unref(options);
    g_variant_unref(shifted);
    if (!decoder ||
        srd_session_metadata_set(session, SRD_CONF_SAMPLERATE, g_variant_new_uint64(50000000)) != SRD_OK ||
        srd_pd_output_callback_add(session, SRD_OUTPUT_ANN, annotation, NULL) != SRD_OK ||
        srd_session_start(session) != SRD_OK ||
        srd_session_send(session, 0, n, pads, n, 1) != SRD_OK)
        goto done;

    printf("SCL rises=%zu final_scl=%u\n", rises, (unsigned)(pads[n-1] & 1));
    rc = 0;
done:
    if (rc)
        fprintf(stderr, "Could not replay the capture with the I2C decoder.\n");
    if (session && srd_session_destroy(session) != SRD_OK)
        rc = 1;
    if (srd_exit() != SRD_OK)
        rc = 1;
    g_free(raw);
    return rc;
}

int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "Usage: %s capture.bin [decoders-directory]\n", argv[0]);
        return 1;
    }
    return replay(argv[1], argc == 3 ? argv[2] : NULL);
}