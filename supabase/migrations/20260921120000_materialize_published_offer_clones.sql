CREATE OR REPLACE FUNCTION public.confirm_source_flyer_offers(
  p_flyer_id uuid
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  confirmed_count bigint;
BEGIN
  UPDATE public.offers
  SET is_confirmed = true,
      offer_kind = 'source_master'
  WHERE flyer_id = p_flyer_id
    AND is_confirmed = false;

  GET DIAGNOSTICS confirmed_count = ROW_COUNT;
  RETURN confirmed_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.materialize_source_flyer_targets(
  p_source_flyer_id uuid
)
RETURNS TABLE (flyer_id uuid, products_count bigint)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.offers (
    name, brand, category, subcategory, offer_key, image_url,
    packshot_source_page, packshot_bbox, flyer_id, supermarket_id,
    supermarket_name, price_original, price_offer, discount_pct,
    unit_price, unit_price_value, unit_price_unit, offer_type, offer_notes,
    valid_from, valid_to, raw_text, confidence_score, format, format_key,
    format_label, is_confirmed, is_reviewed, offer_kind, source_offer_id
  )
  SELECT
    source.name, source.brand, source.category, source.subcategory,
    source.offer_key, source.image_url, source.packshot_source_page,
    source.packshot_bbox, target.id, target.supermarket_id,
    target.supermarket_name, source.price_original, source.price_offer,
    source.discount_pct, source.unit_price, source.unit_price_value,
    source.unit_price_unit, source.offer_type, source.offer_notes,
    source.valid_from, source.valid_to, source.raw_text,
    source.confidence_score, source.format, source.format_key,
    source.format_label, true, source.is_reviewed, 'published_target', source.id
  FROM public.offers AS source
  JOIN public.flyers AS target
    ON target.source_flyer_id = p_source_flyer_id
   AND target.flyer_kind = 'published_target'
  WHERE source.flyer_id = p_source_flyer_id
    AND source.is_confirmed = true
    AND source.offer_kind = 'source_master'
  ON CONFLICT (source_offer_id, supermarket_id)
    WHERE source_offer_id IS NOT NULL
  DO UPDATE SET
    name = EXCLUDED.name,
    brand = EXCLUDED.brand,
    category = EXCLUDED.category,
    subcategory = EXCLUDED.subcategory,
    offer_key = EXCLUDED.offer_key,
    image_url = EXCLUDED.image_url,
    packshot_source_page = EXCLUDED.packshot_source_page,
    packshot_bbox = EXCLUDED.packshot_bbox,
    flyer_id = EXCLUDED.flyer_id,
    supermarket_name = EXCLUDED.supermarket_name,
    price_original = EXCLUDED.price_original,
    price_offer = EXCLUDED.price_offer,
    discount_pct = EXCLUDED.discount_pct,
    unit_price = EXCLUDED.unit_price,
    unit_price_value = EXCLUDED.unit_price_value,
    unit_price_unit = EXCLUDED.unit_price_unit,
    offer_type = EXCLUDED.offer_type,
    offer_notes = EXCLUDED.offer_notes,
    valid_from = EXCLUDED.valid_from,
    valid_to = EXCLUDED.valid_to,
    raw_text = EXCLUDED.raw_text,
    confidence_score = EXCLUDED.confidence_score,
    format = EXCLUDED.format,
    format_key = EXCLUDED.format_key,
    format_label = EXCLUDED.format_label,
    is_confirmed = EXCLUDED.is_confirmed,
    is_reviewed = EXCLUDED.is_reviewed,
    offer_kind = EXCLUDED.offer_kind;

  DELETE FROM public.offers AS clone
  USING public.flyers AS target
  WHERE clone.flyer_id = target.id
    AND target.source_flyer_id = p_source_flyer_id
    AND target.flyer_kind = 'published_target'
    AND clone.offer_kind = 'published_target'
    AND NOT EXISTS (
      SELECT 1
      FROM public.offers AS source
      WHERE source.id = clone.source_offer_id
        AND source.flyer_id = p_source_flyer_id
        AND source.is_confirmed = true
        AND source.offer_kind = 'source_master'
    );

  RETURN QUERY
  WITH target_counts AS (
    SELECT
      target.id,
      COUNT(clone.id)::bigint AS count
    FROM public.flyers AS target
    LEFT JOIN public.offers AS clone
      ON clone.flyer_id = target.id
     AND clone.offer_kind = 'published_target'
    WHERE target.source_flyer_id = p_source_flyer_id
      AND target.flyer_kind = 'published_target'
    GROUP BY target.id
  ), updated AS (
    UPDATE public.flyers AS target
    SET products_count = target_counts.count::integer
    FROM target_counts
    WHERE target.id = target_counts.id
    RETURNING target.id, target.products_count
  )
  SELECT updated.id, updated.products_count::bigint
  FROM updated;
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_source_flyer_offers(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.materialize_source_flyer_targets(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_source_flyer_offers(uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.materialize_source_flyer_targets(uuid) TO service_role;
